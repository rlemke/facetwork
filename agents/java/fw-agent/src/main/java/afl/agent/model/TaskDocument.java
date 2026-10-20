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

import java.util.Map;

/**
 * Represents a task in the tasks collection.
 */
public record TaskDocument(
        String uuid,
        String name,
        String runnerId,
        String workflowId,
        String flowId,
        String stepId,
        String state,
        long created,
        long updated,
        String errorMessage,
        String taskListName,
        String dataType,
        Map<String, Object> data,
        String serverId,
        int retryCount,
        int maxRetries
) {

    /** Constructor without the v1.2 claim fields (server id, retry budget). */
    public TaskDocument(String uuid, String name, String runnerId, String workflowId,
                        String flowId, String stepId, String state, long created, long updated,
                        String errorMessage, String taskListName, String dataType,
                        Map<String, Object> data) {
        this(uuid, name, runnerId, workflowId, flowId, stepId, state, created, updated,
                errorMessage, taskListName, dataType, data, null, 0, 5);
    }

    /**
     * Creates a TaskDocument from a MongoDB document.
     */
    public static TaskDocument fromDocument(Document doc) {
        String errorMessage = null;
        Document error = doc.get("error", Document.class);
        if (error != null) {
            errorMessage = error.getString("message");
        }

        @SuppressWarnings("unchecked")
        Map<String, Object> data = doc.get("data", Map.class);

        return new TaskDocument(
                doc.getString("uuid"),
                doc.getString("name"),
                doc.getString("runner_id"),
                doc.getString("workflow_id"),
                doc.getString("flow_id"),
                doc.getString("step_id"),
                doc.getString("state"),
                asLong(doc.get("created")),
                asLong(doc.get("updated")),
                errorMessage,
                doc.getString("task_list_name"),
                doc.getString("data_type"),
                data,
                doc.getString("server_id"),
                (int) asLong(doc.get("retry_count")),
                doc.get("max_retries") instanceof Number n ? n.intValue() : 5
        );
    }

    /**
     * Reads a numeric field that may be stored as int32 (Integer) or int64
     * (Long) in BSON. Document.getLong() throws ClassCastException on an
     * Integer, which broke task parsing for tasks whose timestamps were written
     * as int32. Returns 0 if absent.
     */
    private static long asLong(Object v) {
        return (v instanceof Number) ? ((Number) v).longValue() : 0L;
    }

    /**
     * Converts to a MongoDB document.
     */
    public Document toDocument() {
        Document doc = new Document()
                .append("uuid", uuid)
                .append("name", name)
                .append("runner_id", runnerId)
                .append("workflow_id", workflowId)
                .append("flow_id", flowId)
                .append("step_id", stepId)
                .append("state", state)
                .append("created", created)
                .append("updated", updated)
                .append("task_list_name", taskListName);

        if (dataType != null) {
            doc.append("data_type", dataType);
        }
        if (data != null) {
            doc.append("data", new Document(data));
        }
        if (errorMessage != null) {
            doc.append("error", new Document("message", errorMessage));
        }
        if (serverId != null) {
            doc.append("server_id", serverId);
        }
        doc.append("retry_count", retryCount);
        doc.append("max_retries", maxRetries);

        return doc;
    }
}
