package fw.agent.model

import org.mongodb.scala.bson.{BsonDocument, BsonValue, Document}
import scala.jdk.CollectionConverters.*

/** A single attribute value from a step's params or returns. */
case class AttributeValue(name: String, value: Any, typeHint: String)

/** Read-only extraction of step attributes from MongoDB documents. */
object StepAttributes:

  /** Extract params from a step document's attributes.params. */
  def extractParams(doc: Document): Map[String, AttributeValue] =
    extractAttributes(doc, "params")

  /** Extract returns from a step document's attributes.returns. */
  def extractReturns(doc: Document): Map[String, AttributeValue] =
    extractAttributes(doc, "returns")

  private def extractAttributes(
      doc: Document,
      section: String
  ): Map[String, AttributeValue] =
    val attrs = for
      attrDoc <- doc.get[BsonDocument]("attributes").toSeq
      sectionDoc <- Option(attrDoc.getDocument(section, null)).toSeq
      key <- sectionDoc.keySet().asScala
    yield
      val entry = sectionDoc.getDocument(key)
      val name = entry.getString("name").getValue
      val value = bsonToScala(entry.get("value"))
      val typeHint = entry.getString("type_hint").getValue
      key -> AttributeValue(name, value, typeHint)
    attrs.toMap

  /** Infer AFL type hint from a Scala value. */
  private[agent] def inferTypeHint(value: Any): String = value match
    case _: Boolean    => "Boolean"
    case _: Int        => "Long"
    case _: Long       => "Long"
    case _: Double     => "Double"
    case _: Float      => "Double"
    case _: String     => "String"
    case _: Seq[?]     => "List"
    case _: Map[?, ?]  => "Map"
    case null          => "Any"
    case _             => "Any"

  /** Convert a BsonValue to a Scala value. */
  private[model] def bsonToScala(bson: BsonValue): Any =
    if bson == null || bson.isNull then null
    else if bson.isString then bson.asString().getValue
    else if bson.isInt32 then bson.asInt32().getValue
    else if bson.isInt64 then bson.asInt64().getValue
    else if bson.isDouble then bson.asDouble().getValue
    else if bson.isBoolean then bson.asBoolean().getValue
    else if bson.isArray then
      bson.asArray().getValues.asScala.map(bsonToScala).toList
    else if bson.isDocument then
      val doc = bson.asDocument()
      doc.keySet().asScala.map(k => k -> bsonToScala(doc.get(k))).toMap
    else bson.toString
