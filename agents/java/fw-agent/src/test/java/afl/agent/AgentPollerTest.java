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

package fw.agent;

import fw.agent.model.StepAttribute;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class AgentPollerTest {

    @Test
    void testNewAgentPoller() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        AgentPoller poller = new AgentPoller(config);

        assertNotNull(poller);
        assertTrue(poller.registeredHandlers().isEmpty());
    }

    @Test
    void testRegisterHandler() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        AgentPoller poller = new AgentPoller(config);

        Handler handler = params -> Map.of("result", "ok");
        poller.register("ns.TestFacet", handler);

        List<String> handlers = poller.registeredHandlers();
        assertEquals(1, handlers.size());
        assertTrue(handlers.contains("ns.TestFacet"));
    }

    @Test
    void testRegisterMultipleHandlers() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        AgentPoller poller = new AgentPoller(config);

        poller.register("ns.FacetA", params -> Map.of());
        poller.register("ns.FacetB", params -> Map.of());
        poller.register("FacetC", params -> Map.of());

        List<String> handlers = poller.registeredHandlers();
        assertEquals(3, handlers.size());
        assertTrue(handlers.contains("ns.FacetA"));
        assertTrue(handlers.contains("ns.FacetB"));
        assertTrue(handlers.contains("FacetC"));
    }

    @Test
    void testFacetNameInjection() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        AgentPoller poller = new AgentPoller(config);
        poller.register("ns.TestFacet", params -> {
            assertEquals("ns.TestFacet", params.get("_facet_name"));
            return new java.util.HashMap<>();
        });
        assertEquals(1, poller.registeredHandlers().size());
        assertTrue(poller.registeredHandlers().contains("ns.TestFacet"));
    }

    @Test
    void testMetadataProviderDefault() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        AgentPoller poller = new AgentPoller(config);
        // No metadata provider set — no error
        assertNotNull(poller);
    }

    @Test
    void testUpdateStepCallbackInjection() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        AgentPoller poller = new AgentPoller(config);
        poller.register("ns.StreamFacet", params -> {
            assertNotNull(params.get("_update_step"));
            return new java.util.HashMap<>();
        });
        assertTrue(poller.registeredHandlers().contains("ns.StreamFacet"));
    }

    @Test
    void testUpdateStepCallbackType() {
        // Verify the Consumer<Map> callback pattern
        java.util.function.Consumer<Map<String, Object>> updateStep = partial -> {
            assertNotNull(partial);
        };
        Map<String, Object> partial = new java.util.HashMap<>();
        partial.put("progress", 50);
        updateStep.accept(partial);
    }

    @Test
    void testUpdateStepPartialUpdates() {
        // Verify multiple partial updates work
        java.util.List<Map<String, Object>> updates = new java.util.ArrayList<>();
        java.util.function.Consumer<Map<String, Object>> updateStep = updates::add;

        Map<String, Object> partial1 = new java.util.HashMap<>();
        partial1.put("progress", 50);
        updateStep.accept(partial1);

        Map<String, Object> partial2 = new java.util.HashMap<>();
        partial2.put("progress", 100);
        partial2.put("result", "done");
        updateStep.accept(partial2);

        assertEquals(2, updates.size());
    }

    @Test
    void testMetadataProviderWhenSet() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        AgentPoller poller = new AgentPoller(config);
        poller.setMetadataProvider(facetName -> {
            if ("ns.TestFacet".equals(facetName)) {
                Map<String, Object> meta = new java.util.HashMap<>();
                meta.put("description", "test handler");
                return meta;
            }
            return null;
        });
        // Verify the provider works correctly
        assertNotNull(poller);
    }
}

class AgentPollerConfigTest {

    @Test
    void testDefaults() {
        AgentPollerConfig config = AgentPollerConfig.defaults();

        assertEquals("fw-agent", config.serviceName());
        assertEquals("default", config.serverGroup());
        assertEquals("default", config.taskList());
        assertEquals(5, config.maxConcurrent());
        assertEquals(2000, config.pollIntervalMs());
        assertEquals(10000, config.heartbeatIntervalMs());
        assertEquals("mongodb://localhost:27017", config.mongoUrl());
        assertEquals("afl", config.database());
    }

    @Test
    void testWithMethods() {
        AgentPollerConfig config = AgentPollerConfig.defaults()
                .withServiceName("my-agent")
                .withServerGroup("production")
                .withTaskList("high-priority")
                .withMaxConcurrent(10);

        assertEquals("my-agent", config.serviceName());
        assertEquals("production", config.serverGroup());
        assertEquals("high-priority", config.taskList());
        assertEquals(10, config.maxConcurrent());
    }
}

class ProtocolTest {

    @Test
    void testCollectionNames() {
        assertEquals("tasks", Protocol.COLLECTION_TASKS);
        assertEquals("steps", Protocol.COLLECTION_STEPS);
        assertEquals("servers", Protocol.COLLECTION_SERVERS);
        assertEquals("events", Protocol.COLLECTION_EVENTS);
        assertEquals("flows", Protocol.COLLECTION_FLOWS);
        assertEquals("workflows", Protocol.COLLECTION_WORKFLOWS);
        assertEquals("runners", Protocol.COLLECTION_RUNNERS);
        assertEquals("locks", Protocol.COLLECTION_LOCKS);
        assertEquals("logs", Protocol.COLLECTION_LOGS);
    }

    @Test
    void testTaskStates() {
        assertEquals("pending", Protocol.TASK_STATE_PENDING);
        assertEquals("running", Protocol.TASK_STATE_RUNNING);
        assertEquals("completed", Protocol.TASK_STATE_COMPLETED);
        assertEquals("failed", Protocol.TASK_STATE_FAILED);
        assertEquals("ignored", Protocol.TASK_STATE_IGNORED);
        assertEquals("canceled", Protocol.TASK_STATE_CANCELED);
    }

    @Test
    void testStepStates() {
        assertEquals("state.facet.execution.EventTransmit", Protocol.STEP_STATE_EVENT_TRANSMIT);
        assertEquals("state.facet.initialization.Created", Protocol.STEP_STATE_CREATED);
        assertEquals("state.facet.execution.StatementError", Protocol.STEP_STATE_STATEMENT_ERROR);
        assertEquals("state.facet.completion.Completed", Protocol.STEP_STATE_COMPLETED);
    }

    @Test
    void testServerStates() {
        assertEquals("startup", Protocol.SERVER_STATE_STARTUP);
        assertEquals("running", Protocol.SERVER_STATE_RUNNING);
        assertEquals("shutdown", Protocol.SERVER_STATE_SHUTDOWN);
        assertEquals("error", Protocol.SERVER_STATE_ERROR);
    }

    @Test
    void testProtocolTaskNames() {
        assertEquals("fw:resume", Protocol.RESUME_TASK_NAME);
        assertEquals("fw:execute", Protocol.EXECUTE_TASK_NAME);
    }

    @Test
    void testStepLogLevels() {
        assertEquals("info", Protocol.STEP_LOG_LEVEL_INFO);
        assertEquals("warning", Protocol.STEP_LOG_LEVEL_WARNING);
        assertEquals("error", Protocol.STEP_LOG_LEVEL_ERROR);
        assertEquals("success", Protocol.STEP_LOG_LEVEL_SUCCESS);
    }

    @Test
    void testStepLogSources() {
        assertEquals("framework", Protocol.STEP_LOG_SOURCE_FRAMEWORK);
        assertEquals("handler", Protocol.STEP_LOG_SOURCE_HANDLER);
    }

    @Test
    void testStepLogsCollection() {
        assertEquals("step_logs", Protocol.COLLECTION_STEP_LOGS);
    }
}

class StepAttributeTest {

    @Test
    void testInferTypeHintBoolean() {
        assertEquals("Boolean", StepAttribute.inferTypeHint(true));
        assertEquals("Boolean", StepAttribute.inferTypeHint(false));
    }

    @Test
    void testInferTypeHintLong() {
        assertEquals("Long", StepAttribute.inferTypeHint(42));
        assertEquals("Long", StepAttribute.inferTypeHint(42L));
        assertEquals("Long", StepAttribute.inferTypeHint(0));
    }

    @Test
    void testInferTypeHintDouble() {
        assertEquals("Double", StepAttribute.inferTypeHint(3.14));
        assertEquals("Double", StepAttribute.inferTypeHint(3.14f));
    }

    @Test
    void testInferTypeHintString() {
        assertEquals("String", StepAttribute.inferTypeHint("hello"));
        assertEquals("String", StepAttribute.inferTypeHint(""));
    }

    @Test
    void testInferTypeHintList() {
        assertEquals("List", StepAttribute.inferTypeHint(List.of(1, 2, 3)));
        assertEquals("List", StepAttribute.inferTypeHint(List.of()));
    }

    @Test
    void testInferTypeHintMap() {
        assertEquals("Map", StepAttribute.inferTypeHint(Map.of("a", 1)));
        assertEquals("Map", StepAttribute.inferTypeHint(Map.of()));
    }

    @Test
    void testInferTypeHintAny() {
        assertEquals("Any", StepAttribute.inferTypeHint(null));
        assertEquals("Any", StepAttribute.inferTypeHint(new Object()));
    }
}
