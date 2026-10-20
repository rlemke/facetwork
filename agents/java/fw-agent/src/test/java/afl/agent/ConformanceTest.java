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

import afl.agent.model.TaskDocument;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoDatabase;
import org.bson.Document;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

import java.io.File;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/**
 * This library against the protocol's shared conformance cases
 * (agents/protocol/conformance). The Python runtime runs the same files in
 * tests/runtime/test_agent_protocol_conformance.py, so a rule exists once.
 *
 * <p>The retry cases are pure. The claim and lifecycle cases need a real
 * MongoDB: set FW_AGENT_TEST_MONGO_URL (e.g. a throwaway {@code mongo:7}
 * container); they are skipped otherwise. A scratch database is created and
 * dropped.
 */
class ConformanceTest {

    private static final File CONF = new File("../../protocol/conformance");
    private static final ObjectMapper JSON = new ObjectMapper();
    private static MongoClient client;
    private static MongoDatabase db;

    @BeforeAll
    static void connect() {
        String url = System.getenv("FW_AGENT_TEST_MONGO_URL");
        if (url != null && !url.isEmpty()) {
            client = MongoClients.create(url);
            db = client.getDatabase("fw_agent_conformance_" + UUID.randomUUID().toString().substring(0, 8));
        }
    }

    @AfterAll
    static void drop() {
        if (db != null) {
            db.drop();
            client.close();
        }
    }

    private static void requireMongo() {
        Assumptions.assumeTrue(db != null, "FW_AGENT_TEST_MONGO_URL not set");
    }

    // --- retry transition (pure) ---------------------------------------------

    @TestFactory
    List<DynamicTest> retryCases() throws Exception {
        JsonNode root = JSON.readTree(new File(CONF, "retry_cases.json"));
        List<DynamicTest> tests = new ArrayList<>();
        for (JsonNode c : root.get("cases")) {
            tests.add(DynamicTest.dynamicTest(c.get("id").asText(), () -> {
                long now = 1_000_000L;
                MongoOps.FailTransition t = MongoOps.failTransition(
                        c.get("retry_count").asInt(), c.get("max_retries").asInt(),
                        c.get("permanent").asBoolean(), now);
                assertEquals(c.get("state").asText(), t.state());
                assertEquals(c.get("retry_count_after").asInt(), t.retryCount());
                if (c.has("backoff_ms")) {
                    assertEquals(c.get("backoff_ms").asLong(), t.nextRetryAfter() - now);
                }
            }));
        }
        return tests;
    }

    @Test
    void specVersionAndTimingMatchThisLibrary() throws Exception {
        JsonNode spec = JSON.readTree(new File(CONF.getParentFile(), "constants.json"));
        assertEquals(spec.get("version").asText(), Protocol.PROTOCOL_VERSION);
        JsonNode timing = spec.get("timing");
        assertEquals(timing.get("lease_ms").asLong(), Protocol.LEASE_MS);
        assertEquals(timing.get("lease_reclaim_scan_ms").asLong(), Protocol.LEASE_RECLAIM_SCAN_MS);
        assertEquals(timing.get("default_max_retries").asInt(), Protocol.DEFAULT_MAX_RETRIES);
    }

    // --- claim (live) ----------------------------------------------------------

    @TestFactory
    List<DynamicTest> claimCases() throws Exception {
        JsonNode root = JSON.readTree(new File(CONF, "claim_cases.json"));
        JsonNode srv = root.get("server");
        List<String> names = strings(srv.get("task_names"));
        List<String> lists = strings(srv.get("task_lists"));
        Map<String, Object> resources = new LinkedHashMap<>();
        srv.get("resources").fields().forEachRemaining(e -> resources.put(e.getKey(), e.getValue().asDouble()));

        List<DynamicTest> tests = new ArrayList<>();
        for (JsonNode c : root.get("cases")) {
            tests.add(DynamicTest.dynamicTest(c.get("id").asText(), () -> {
                requireMongo();
                var tasks = db.getCollection(Protocol.COLLECTION_TASKS);
                tasks.drop();
                long now = System.currentTimeMillis();
                String uuid = "t-" + c.get("id").asText();
                Document doc = new Document("uuid", uuid).append("runner_id", "r")
                        .append("workflow_id", "w").append("flow_id", "f")
                        .append("step_id", "s-" + uuid).append("created", now).append("updated", now);
                for (Iterator<Map.Entry<String, JsonNode>> it = c.get("task").fields(); it.hasNext(); ) {
                    Map.Entry<String, JsonNode> e = it.next();
                    doc.append(e.getKey(), value(e.getValue(), now));
                }
                tasks.insertOne(doc);

                Optional<TaskDocument> got = new MongoOps(db).claimTask(names, lists,
                        "conformance-server", resources, true);
                assertEquals(c.get("claimed").asBoolean(), got.isPresent());
                if (got.isPresent()) {
                    Document stored = tasks.find(new Document("uuid", uuid)).first();
                    assertEquals("running", stored.getString("state"));
                    assertEquals("conformance-server", stored.getString("server_id"));
                    assertTrue(((Number) stored.get("lease_expires")).longValue() > now);
                    if (c.has("retry_count_after")) {
                        assertEquals(c.get("retry_count_after").asInt(),
                                ((Number) stored.get("retry_count")).intValue());
                    }
                }
            }));
        }
        return tests;
    }

    // --- the rest of the lifecycle (live) --------------------------------------

    private static TaskDocument claimOne(MongoOps ops, String server, Document extra) {
        var tasks = db.getCollection(Protocol.COLLECTION_TASKS);
        tasks.drop();
        long now = System.currentTimeMillis();
        Document doc = new Document("uuid", "life").append("name", "ns.Facet")
                .append("task_list_name", "ns").append("state", "pending")
                .append("step_id", "s").append("workflow_id", "w")
                .append("created", now).append("updated", now);
        doc.putAll(extra);
        tasks.insertOne(doc);
        return ops.claimTask(List.of("ns.Facet"), List.of("ns"), server, Map.of(), false).orElseThrow();
    }

    private static Document stored() {
        return db.getCollection(Protocol.COLLECTION_TASKS).find(new Document("uuid", "life")).first();
    }

    @Test
    void completionIsOwnershipGated() {
        requireMongo();
        MongoOps ops = new MongoOps(db);
        TaskDocument t = claimOne(ops, "me", new Document());
        assertFalse(ops.completeTask(t, "someone-else"));
        assertEquals("running", stored().getString("state"));
        assertTrue(ops.completeTask(t, "me"));
        assertEquals("completed", stored().getString("state"));
    }

    @Test
    void failureRetriesWithBackoffThenDeadLetters() {
        requireMongo();
        MongoOps ops = new MongoOps(db);
        TaskDocument t = claimOne(ops, "me", new Document("retry_count", 0).append("max_retries", 2));
        MongoOps.FailTransition first = ops.failTask(t, "me", "boom", false).orElseThrow();
        Document d = stored();
        assertEquals("pending", d.getString("state"));
        assertEquals("", d.getString("server_id"));
        assertEquals(1, ((Number) d.get("retry_count")).intValue());
        assertEquals(first.nextRetryAfter(), ((Number) d.get("next_retry_after")).longValue());
        assertEquals("boom", ((Document) d.get("error")).getString("message"));
        // Cooling down: not claimable yet.
        assertTrue(ops.claimTask(List.of("ns.Facet"), List.of("ns"), "me", Map.of(), false).isEmpty());

        db.getCollection(Protocol.COLLECTION_TASKS).updateOne(new Document("uuid", "life"),
                new Document("$set", new Document("next_retry_after", 0)));
        TaskDocument again = ops.claimTask(List.of("ns.Facet"), List.of("ns"), "me", Map.of(), false).orElseThrow();
        assertTrue(ops.failTask(again, "me", "boom", false).orElseThrow().deadLettered());
        assertEquals("dead_letter", stored().getString("state"));
    }

    @Test
    void permanentErrorDeadLettersAtOnce() {
        requireMongo();
        MongoOps ops = new MongoOps(db);
        TaskDocument t = claimOne(ops, "me", new Document());
        assertTrue(ops.failTask(t, "me", "bad input", true).orElseThrow().deadLettered());
        Document d = stored();
        assertEquals("dead_letter", d.getString("state"));
        assertEquals(0, ((Number) d.get("retry_count")).intValue());
        assertTrue(((Document) d.get("error")).getBoolean("permanent"));
    }

    @Test
    void failureOnAReclaimedTaskWritesNothing() {
        requireMongo();
        MongoOps ops = new MongoOps(db);
        TaskDocument t = claimOne(ops, "me", new Document());
        assertTrue(ops.failTask(t, "not-me", "boom", false).isEmpty());
        assertEquals("running", stored().getString("state"));
    }

    @Test
    void leaseRenewalAndCancellationFollowOwnership() {
        requireMongo();
        MongoOps ops = new MongoOps(db);
        TaskDocument t = claimOne(ops, "me", new Document());
        long before = ((Number) stored().get("lease_expires")).longValue();
        assertTrue(ops.renewLease(t.uuid(), "me", 40, "halfway"));
        Document d = stored();
        assertTrue(((Number) d.get("lease_expires")).longValue() >= before);
        assertEquals(40, ((Number) d.get("progress_pct")).intValue());
        assertTrue(ops.cancellationReason(t.uuid(), "me").isEmpty());

        // Reclaimed by another server.
        db.getCollection(Protocol.COLLECTION_TASKS).updateOne(new Document("uuid", "life"),
                new Document("$set", new Document("server_id", "other")));
        assertFalse(ops.renewLease(t.uuid(), "me", null, null));
        assertTrue(ops.cancellationReason(t.uuid(), "me").orElseThrow().contains("reclaimed"));

        db.getCollection(Protocol.COLLECTION_TASKS).updateOne(new Document("uuid", "life"),
                new Document("$set", new Document("state", "canceled")));
        assertTrue(ops.cancellationReason(t.uuid(), "me").orElseThrow().contains("canceled"));
    }

    @Test
    void heartbeatReportsAPrunedRecordAndPublishesFreeMemory() {
        requireMongo();
        var servers = db.getCollection(Protocol.COLLECTION_SERVERS);
        servers.drop();
        ServerRegistration reg = new ServerRegistration(db);
        AgentPollerConfig cfg = AgentPollerConfig.defaults();
        assertFalse(reg.heartbeat("srv"));                      // no record yet
        reg.register("srv", cfg, List.of("ns.Facet"));
        Document s = servers.find(new Document("uuid", "srv")).first();
        assertTrue(((Document) s.get("resources")).containsKey("memory_gb"));
        assertEquals(cfg.taskList(), s.getString("task_list"));
        assertTrue(reg.heartbeat("srv"));
        s = servers.find(new Document("uuid", "srv")).first();
        assertTrue(((Document) s.get("free_resources")).containsKey("memory_gb"));

        servers.updateOne(new Document("uuid", "srv"),
                new Document("$set", new Document("state", "shutdown")));
        assertFalse(reg.heartbeat("srv"));                      // marked shutdown: re-register
        servers.updateOne(new Document("uuid", "srv"),
                new Document("$set", new Document("state", "quarantine")));
        assertTrue(reg.heartbeat("srv"));                       // still live, not resurrected
        assertEquals("quarantine", servers.find(new Document("uuid", "srv")).first().getString("state"));
    }

    private static List<String> strings(JsonNode arr) {
        List<String> out = new ArrayList<>();
        arr.forEach(n -> out.add(n.asText()));
        return out;
    }

    private static Object value(JsonNode v, long now) {
        if (v.isObject() && v.size() == 1 && v.has("rel_ms")) {
            return now + v.get("rel_ms").asLong();
        }
        if (v.isObject()) {
            Document d = new Document();
            v.fields().forEachRemaining(e -> d.append(e.getKey(), value(e.getValue(), now)));
            return d;
        }
        if (v.isArray()) {
            List<Object> l = new ArrayList<>();
            v.forEach(x -> l.add(value(x, now)));
            return l;
        }
        if (v.isIntegralNumber()) {
            return v.asLong();
        }
        if (v.isNumber()) {
            return v.asDouble();
        }
        return v.asText();
    }
}
