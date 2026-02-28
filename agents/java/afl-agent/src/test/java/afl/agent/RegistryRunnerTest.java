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

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class RegistryRunnerTest {

    @Test
    void testEmptyActiveTopicsReturnsNoEffectiveHandlers() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        RegistryRunner runner = new RegistryRunner(config);

        runner.register("ns.FacetA", params -> Map.of());
        runner.register("ns.FacetB", params -> Map.of());

        List<String> effective = runner.effectiveHandlers();
        assertTrue(effective.isEmpty(),
                "Expected no effective handlers with no active topics");
    }

    @Test
    void testDelegateRegisterToPoller() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        RegistryRunner runner = new RegistryRunner(config);

        runner.register("ns.FacetA", params -> Map.of("result", "ok"));

        List<String> handlers = runner.registeredHandlers();
        assertTrue(handlers.contains("ns.FacetA"));
    }

    @Test
    void testDefaultRefreshInterval() {
        // RegistryRunner with default constructor uses 30000ms
        AgentPollerConfig config = AgentPollerConfig.defaults();
        RegistryRunner runner = new RegistryRunner(config);

        // No exception means construction succeeded with default interval
        assertNotNull(runner);
    }

    @Test
    void testCustomRefreshInterval() {
        AgentPollerConfig config = AgentPollerConfig.defaults();
        RegistryRunner runner = new RegistryRunner(config, 5000);

        assertNotNull(runner);
    }

    @Test
    void testHandlerRegistrationsConstant() {
        assertEquals("handler_registrations", Protocol.COLLECTION_HANDLER_REGISTRATIONS);
    }
}
