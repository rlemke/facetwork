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

package afl.agent;

import afl.agent.model.StepAttribute;
import afl.agent.model.TaskDocument;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.FindOneAndUpdateOptions;
import com.mongodb.client.model.ReturnDocument;
import com.mongodb.client.model.Updates;
import org.bson.Document;
import org.bson.conversions.Bson;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static com.mongodb.client.model.Filters.*;

/**
 * MongoDB operations for the AFL agent protocol.
 */
public class MongoOps {

    private final MongoDatabase db;

    public MongoOps(MongoDatabase db) {
        this.db = db;
    }

    /**
     * Atomically claims a pending task for processing.
     * Returns empty if no task is available.
     */
    public Optional<TaskDocument> claimTask(List<String> taskNames, String taskList) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_TASKS);

        Bson filter = and(
                eq("state", Protocol.TASK_STATE_PENDING),
                in("name", taskNames),
                eq("task_list_name", taskList)
        );

        Bson update = Updates.combine(
                Updates.set("state", Protocol.TASK_STATE_RUNNING),
                Updates.set("updated", nowMillis())
        );

        FindOneAndUpdateOptions options = new FindOneAndUpdateOptions()
                .returnDocument(ReturnDocument.AFTER);

        Document doc = collection.findOneAndUpdate(filter, update, options);

        if (doc == null) {
            return Optional.empty();
        }

        return Optional.of(TaskDocument.fromDocument(doc));
    }

    /**
     * Reads the params attribute from a step.
     */
    public Map<String, Object> readStepParams(String stepId) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEPS);

        Document step = collection.find(eq("uuid", stepId)).first();
        if (step == null) {
            throw new IllegalArgumentException("Step not found: " + stepId);
        }

        Map<String, Object> result = new HashMap<>();

        Document attributes = step.get("attributes", Document.class);
        if (attributes != null) {
            Document params = attributes.get("params", Document.class);
            if (params != null) {
                for (String name : params.keySet()) {
                    Document attr = params.get(name, Document.class);
                    if (attr != null) {
                        result.put(name, attr.get("value"));
                    }
                }
            }
        }

        return result;
    }

    /**
     * Writes return attributes to a step.
     */
    public void writeStepReturns(String stepId, Map<String, Object> returns) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEPS);

        Document setFields = new Document();
        for (Map.Entry<String, Object> entry : returns.entrySet()) {
            String name = entry.getKey();
            Object value = entry.getValue();

            Document attr = new Document()
                    .append("name", name)
                    .append("value", value)
                    .append("type_hint", StepAttribute.inferTypeHint(value));

            setFields.append("attributes.returns." + name, attr);
        }

        collection.updateOne(
                and(
                        eq("uuid", stepId),
                        eq("state", Protocol.STEP_STATE_EVENT_TRANSMIT)
                ),
                new Document("$set", setFields)
        );
    }

    /**
     * Marks a task as completed.
     */
    public void markTaskCompleted(TaskDocument task) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_TASKS);

        collection.updateOne(
                eq("uuid", task.uuid()),
                Updates.combine(
                        Updates.set("state", Protocol.TASK_STATE_COMPLETED),
                        Updates.set("updated", nowMillis())
                )
        );
    }

    /**
     * Marks a task as failed with an error message.
     */
    public void markTaskFailed(TaskDocument task, String errorMsg) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_TASKS);

        collection.updateOne(
                eq("uuid", task.uuid()),
                Updates.combine(
                        Updates.set("state", Protocol.TASK_STATE_FAILED),
                        Updates.set("updated", nowMillis()),
                        Updates.set("error", new Document("message", errorMsg))
                )
        );
    }

    /**
     * Inserts a step log entry for dashboard observability.
     * Best-effort: errors are caught and logged.
     */
    public void insertStepLog(String stepId, String workflowId, String runnerId,
                              String facetName, String source, String level, String message) {
        try {
            MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEP_LOGS);

            long now = nowMillis();
            Document doc = new Document()
                    .append("uuid", UUID.randomUUID().toString())
                    .append("step_id", stepId)
                    .append("workflow_id", workflowId)
                    .append("runner_id", runnerId)
                    .append("facet_name", facetName)
                    .append("source", source)
                    .append("level", level)
                    .append("message", message)
                    .append("details", new Document())
                    .append("time", now);

            collection.insertOne(doc);
        } catch (Exception e) {
            java.util.logging.Logger.getLogger(MongoOps.class.getName())
                    .fine("Could not save step log for step " + stepId + ": " + e.getMessage());
        }
    }

    /**
     * Inserts an afl:resume task for the Python RunnerService.
     */
    public void insertResumeTask(String stepId, String workflowId, String taskList) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_TASKS);

        long now = nowMillis();
        Document task = new Document()
                .append("uuid", UUID.randomUUID().toString())
                .append("name", Protocol.RESUME_TASK_NAME)
                .append("runner_id", "")
                .append("workflow_id", workflowId)
                .append("flow_id", "")
                .append("step_id", stepId)
                .append("state", Protocol.TASK_STATE_PENDING)
                .append("created", now)
                .append("updated", now)
                .append("task_list_name", taskList)
                .append("data_type", "resume")
                .append("data", new Document()
                        .append("step_id", stepId)
                        .append("workflow_id", workflowId));

        collection.insertOne(task);
    }

    /**
     * Returns current time in milliseconds since Unix epoch.
     */
    public static long nowMillis() {
        return System.currentTimeMillis();
    }
}
