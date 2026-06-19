# Bash completion for `fw`. Enable with:  source "$(fw --completion-path)"
# (zsh: run `autoload -U +X bashcompinit && bashcompinit` first.)
_fw_complete() {
    local cur prev words cword
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    local lib
    lib="$(fw --completion-path 2>/dev/null)"; lib="${lib%/_helpers/*}"   # -> scripts/lib
    [ -d "$lib" ] || return 0
    local depth=$((COMP_CWORD))
    if [ "$depth" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$(cd "$lib" && ls -d */ 2>/dev/null | grep -v '^_' | tr -d /)" -- "$cur") )
    elif [ "$depth" -ge 2 ]; then
        local p="$lib"; local i
        for ((i=1; i<COMP_CWORD; i++)); do p="$p/${COMP_WORDS[i]}"; done
        p="$(dirname "$p")"   # parent of the token being completed
        [ -d "$p" ] && COMPREPLY=( $(compgen -W "$(cd "$p" && ls 2>/dev/null | grep -v '^_')" -- "$cur") )
    fi
}
complete -F _fw_complete fw
