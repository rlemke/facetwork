// Copyright 2025 Ralph Lemke
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package afl.agent.model;

import org.bson.Document;

import java.util.List;
import java.util.Map;

/**
 * Represents a parameter or return value attribute.
 */
public record StepAttribute(
        String name,
        Object value,
        String typeHint
) {

    /**
     * Creates a StepAttribute from a MongoDB document.
     */
    public static StepAttribute fromDocument(Document doc) {
        return new StepAttribute(
                doc.getString("name"),
                doc.get("value"),
                doc.getString("type_hint")
        );
    }

    /**
     * Converts to a MongoDB document.
     */
    public Document toDocument() {
        Document doc = new Document()
                .append("name", name)
                .append("value", value);

        if (typeHint != null) {
            doc.append("type_hint", typeHint);
        }

        return doc;
    }

    /**
     * Infers a type hint string from a Java value.
     */
    public static String inferTypeHint(Object value) {
        if (value instanceof Boolean) {
            return "Boolean";
        }
        if (value instanceof Long || value instanceof Integer) {
            return "Long";
        }
        if (value instanceof Double || value instanceof Float) {
            return "Double";
        }
        if (value instanceof String) {
            return "String";
        }
        if (value instanceof List) {
            return "List";
        }
        if (value instanceof Map) {
            return "Map";
        }
        return "Any";
    }
}
