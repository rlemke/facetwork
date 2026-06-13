"""Build and query a facet capability index from compiled FFL.

The input is a compiled program dict — either ``emit_dict(parse(source))`` or a
stored flow's ``compiled_ast`` — shaped as::

    {"type": "Program", "declarations": [
        {"type": "Namespace", "name": "osm.Spatial", "declarations": [
            {"type": "EventFacetDecl", "name": "WithinDistance",
             "doc": {"description": "..."},
             "params":  [{"name": "...", "type": "...", "default": {...}}],
             "returns": [{"name": "result", "type": "SpatialResult"}]},
            ...]},
        ...]}

Namespaces may nest; facets may also appear at the top level. The index is a
flat list of :class:`FacetCapability`, searchable by free text / namespace /
kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_FACET_TYPES = {"FacetDecl", "EventFacetDecl"}


@dataclass
class FacetParam:
    """One parameter or return value of a facet."""

    name: str
    type: str
    has_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = {"name": self.name, "type": self.type}
        if self.has_default:
            d["has_default"] = True
        return d


@dataclass
class FacetCapability:
    """A single facet's capability record."""

    qualified_name: str          # e.g. "osm.Spatial.WithinDistance"
    name: str                    # e.g. "WithinDistance"
    namespace: str               # e.g. "osm.Spatial" ("" for top-level)
    purpose: str                 # first line of the doc comment
    doc: str                     # full doc-comment description
    is_event: bool
    params: list[FacetParam] = field(default_factory=list)
    returns: list[FacetParam] = field(default_factory=list)
    mixins: list[str] = field(default_factory=list)
    # Effect/cost annotations (from `with Effect(kind=…)` / `with Cost(tier=…)`
    # mixins, or inferred from `with Timeout(minutes=…)`); "" when unknown. Let the
    # composer prefer pure/cheap primitives and know which steps hit an engine.
    effect: str = ""             # "" | "pure" | "external" | "io"
    cost: str = ""               # "" | "free" | "cheap" | "moderate" | "expensive"
    # Ownership annotations (from `with Author(email=…)` / `with Teams(names=[…])`).
    author: str = ""             # author email; "" when unknown
    teams: list[str] = field(default_factory=list)  # teams this facet/flow belongs to

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified_name": self.qualified_name,
            "name": self.name,
            "namespace": self.namespace,
            "purpose": self.purpose,
            "is_event": self.is_event,
            "signature": self.signature,
            "effect": self.effect,
            "cost": self.cost,
            "author": self.author,
            "teams": self.teams,
            "params": [p.to_dict() for p in self.params],
            "returns": [r.to_dict() for r in self.returns],
            "mixins": self.mixins,
        }

    @property
    def signature(self) -> str:
        """A compact human-readable signature, e.g.
        ``osm.Spatial.WithinDistance(subject_path: String, …) => (result: SpatialResult)``."""
        ps = ", ".join(f"{p.name}: {p.type}" for p in self.params)
        rs = ", ".join(f"{r.name}: {r.type}" for r in self.returns)
        kw = "event facet " if self.is_event else "facet "
        return f"{kw}{self.qualified_name}({ps})" + (f" => ({rs})" if rs else "")


def _type_to_str(node: Any) -> str:
    """Render a type node (string, ``[T]`` array, or dict) as a string."""
    if node is None:
        return "Any"
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        t = node.get("type")
        if t in ("ArrayType", "Array"):
            elem = node.get("element") or node.get("elementType") or node.get("inner")
            return f"[{_type_to_str(elem)}]"
        return node.get("name") or node.get("type") or "Any"
    return str(node)


def _params(raw: list[dict] | None) -> list[FacetParam]:
    out: list[FacetParam] = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        out.append(FacetParam(
            name=p.get("name", ""),
            type=_type_to_str(p.get("type")),
            has_default="default" in p and p.get("default") is not None,
        ))
    return out


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return ""


# Cost tiers, cheapest → most expensive (for the max_cost ceiling filter).
COST_ORDER = {"free": 0, "cheap": 1, "moderate": 2, "expensive": 3}


def _mixin_arg(mixin: dict, *names: str):
    """Value of the first matching named arg of a mixin (unwraps {value: …})."""
    for arg in mixin.get("args") or []:
        if arg.get("name") in names:
            v = arg.get("value")
            return v.get("value") if isinstance(v, dict) and "value" in v else v
    return None


def _mixin_str_list(value: Any) -> list[str]:
    """Coerce a mixin arg value to a list of strings.

    Handles an emitted ``ArrayLiteral`` (``{type, elements:[{value}, …]}``), a
    raw list, or a single scalar.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        elems = value.get("elements")
        if isinstance(elems, list):
            return [
                str(e.get("value"))
                for e in elems
                if isinstance(e, dict) and e.get("value") is not None
            ]
        if "value" in value and value["value"] is not None:
            return [str(value["value"])]
        return []
    if isinstance(value, list):
        return [str(v.get("value") if isinstance(v, dict) else v) for v in value]
    return [str(value)]


def author_and_teams(mixins_raw: list | None) -> tuple[str, list[str]]:
    """Extract ``(author_email, team_names)`` from a node's mixins.

    Reads ``with Author(email=…)`` and ``with Teams(names=[…])`` — the ownership
    annotation mixins that a workflow or facet may declare. Public so the
    submit/seed/catalog paths can tag runs and flows from the same source of
    truth the capability index uses. Returns ``("", [])`` when absent.
    """
    author = ""
    teams: list[str] = []
    for m in mixins_raw or []:
        if not isinstance(m, dict):
            continue
        target = m.get("target") or m.get("name") or ""
        if target == "Author":
            author = str(_mixin_arg(m, "email", "user") or "") or author
        elif target == "Teams":
            teams = _mixin_str_list(_mixin_arg(m, "names", "teams")) or teams
    return author, teams


def _annotations(mixins_raw: list | None) -> tuple[list[str], str, str]:
    """Parse a facet's mixins into (mixin target names, effect, cost).

    Recognizes the annotation mixins ``Effect(kind=…)`` and ``Cost(tier=…)``;
    falls back to inferring cost from ``Timeout(minutes=…)`` (≥30 min →
    ``expensive``, else ``moderate``) when no explicit ``Cost`` is given.
    """
    names: list[str] = []
    effect = cost = ""
    timeout_min: float | None = None
    for m in mixins_raw or []:
        if not isinstance(m, dict):
            continue
        target = m.get("target") or m.get("name") or ""
        if not target:
            continue
        names.append(target)
        if target == "Effect":
            effect = str(_mixin_arg(m, "kind", "effect") or "") or effect
        elif target == "Cost":
            cost = str(_mixin_arg(m, "tier", "weight") or "") or cost
        elif target == "Timeout":
            mins = _mixin_arg(m, "minutes")
            try:
                timeout_min = float(mins) if mins is not None else timeout_min
            except (TypeError, ValueError):
                pass
    if not cost and timeout_min is not None:
        cost = "expensive" if timeout_min >= 30 else "moderate"
    return names, effect, cost


def _facet_capability(decl: dict, namespace: str) -> FacetCapability:
    name = decl.get("name", "")
    qualified = f"{namespace}.{name}" if namespace else name
    doc = (decl.get("doc") or {}).get("description", "") if isinstance(decl.get("doc"), dict) else ""
    mixins, effect, cost = _annotations(decl.get("mixins"))
    author, teams = author_and_teams(decl.get("mixins"))
    return FacetCapability(
        qualified_name=qualified,
        name=name,
        namespace=namespace,
        purpose=_first_line(doc),
        doc=doc,
        is_event=decl.get("type") == "EventFacetDecl",
        params=_params(decl.get("params")),
        returns=_params(decl.get("returns")),
        mixins=mixins,
        effect=effect,
        cost=cost,
        author=author,
        teams=teams,
    )


def _walk(decls: list, namespace: str, out: list[FacetCapability]) -> None:
    for decl in decls or []:
        if not isinstance(decl, dict):
            continue
        t = decl.get("type")
        if t == "Namespace":
            ns_name = decl.get("name", "")
            child_ns = f"{namespace}.{ns_name}" if namespace and ns_name else (ns_name or namespace)
            _walk(decl.get("declarations") or decl.get("body") or [], child_ns, out)
        elif t in _FACET_TYPES:
            out.append(_facet_capability(decl, namespace))


def index_program(program: dict) -> list[FacetCapability]:
    """Index every facet/event-facet in one compiled program dict."""
    if not isinstance(program, dict):
        return []
    out: list[FacetCapability] = []
    _walk(program.get("declarations") or [], "", out)
    return out


def index_programs(programs: list[dict]) -> list[FacetCapability]:
    """Index several programs, de-duplicating by qualified name (first wins)."""
    seen: dict[str, FacetCapability] = {}
    for prog in programs:
        for cap in index_program(prog):
            seen.setdefault(cap.qualified_name, cap)
    return sorted(seen.values(), key=lambda c: c.qualified_name)


def _score(cap: FacetCapability, terms: list[str]) -> int:
    """Relevance score for a capability against query terms (higher = better)."""
    if not terms:
        return 0
    name = cap.name.lower()
    qn = cap.qualified_name.lower()
    purpose = cap.purpose.lower()
    doc = cap.doc.lower()
    type_text = " ".join(p.type.lower() for p in cap.params + cap.returns)
    param_text = " ".join(p.name.lower() for p in cap.params + cap.returns)
    score = 0
    for term in terms:
        if term == name:
            score += 20
        elif term in name or term in qn:
            score += 10
        if term in purpose:
            score += 6
        elif term in doc:
            score += 3
        if term in type_text:
            score += 4
        if term in param_text:
            score += 2
    return score


def search(
    caps: list[FacetCapability],
    query: str = "",
    namespace: str = "",
    kind: str = "all",
    limit: int = 0,
    effect: str = "",
    max_cost: str = "",
) -> list[FacetCapability]:
    """Filter + rank capabilities.

    * ``query`` — free text matched (AND across whitespace-separated terms)
      against name / qualified name / purpose / doc / param+return names+types.
    * ``namespace`` — prefix match on the facet's namespace (e.g. ``osm.Spatial``
      or just ``osm``).
    * ``kind`` — ``"facet"`` | ``"event_facet"`` | ``"all"``.
    * ``effect`` — keep only facets with this declared effect (``pure`` / ``external``
      / ``io``); un-annotated facets are excluded (you asked for a *known* effect).
    * ``max_cost`` — drop facets whose *known* cost tier exceeds this ceiling
      (``free`` < ``cheap`` < ``moderate`` < ``expensive``); un-annotated cost passes.
    * ``limit`` — cap the result count (0 = no cap).

    Results are ranked by relevance when ``query`` is given, else by qualified name.
    """
    terms = [t for t in query.lower().split() if t]
    ns = namespace.lower().strip()
    eff = effect.lower().strip()
    cost_ceiling = COST_ORDER.get(max_cost.lower().strip())
    result: list[tuple[int, FacetCapability]] = []
    for cap in caps:
        if kind == "facet" and cap.is_event:
            continue
        if kind == "event_facet" and not cap.is_event:
            continue
        if ns and not (cap.namespace.lower() == ns or cap.namespace.lower().startswith(ns + ".")):
            continue
        if eff and cap.effect.lower() != eff:
            continue
        if cost_ceiling is not None and cap.cost and COST_ORDER.get(cap.cost.lower(), 99) > cost_ceiling:
            continue
        if terms:
            score = _score(cap, terms)
            # AND semantics: every term must hit something.
            if any(_score(cap, [t]) == 0 for t in terms):
                continue
            result.append((score, cap))
        else:
            result.append((0, cap))
    if terms:
        result.sort(key=lambda sc: (-sc[0], sc[1].qualified_name))
    else:
        result.sort(key=lambda sc: sc[1].qualified_name)
    ranked = [c for _, c in result]
    return ranked[:limit] if limit and limit > 0 else ranked
