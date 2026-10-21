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

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

/** The wire contract with {@code facetwork.runtime.claimer} (POST /next, POST /kick). */
class ClaimProxyTest {

    private HttpServer server;
    private final AtomicReference<JsonNode> lastBody = new AtomicReference<>();
    private final AtomicReference<String> reply = new AtomicReference<>("{\"task\": null}");
    private final AtomicInteger status = new AtomicInteger(200);
    private final CountDownLatch kicked = new CountDownLatch(1);

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/next", ex -> {
            lastBody.set(new ObjectMapper().readTree(ex.getRequestBody()));
            byte[] out = reply.get().getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(status.get(), out.length);
            ex.getResponseBody().write(out);
            ex.close();
        });
        server.createContext("/kick", ex -> {
            kicked.countDown();
            ex.sendResponseHeaders(200, -1);
            ex.close();
        });
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    private ClaimProxy proxy() {
        return new ClaimProxy("http://127.0.0.1:" + server.getAddress().getPort() + "/");
    }

    @Test
    void sendsTheSameSpecADirectClaimUses() throws IOException {
        reply.set("{\"task\": \"t-1\"}");
        Optional<String> got = proxy().next("srv-1", List.of("osm.Routing.GraphHopper.RouteBatch"),
                List.of("osm"), Map.of("memory_gb", 6.5), 5.0, true);
        assertEquals(Optional.of("t-1"), got);
        JsonNode b = lastBody.get();
        assertEquals("srv-1", b.get("server_id").asText());
        // claims = [[names, lists]], as RunnerService sends it
        assertEquals("osm.Routing.GraphHopper.RouteBatch", b.get("claims").get(0).get(0).get(0).asText());
        assertEquals("osm", b.get("claims").get(0).get(1).get(0).asText());
        // an agent runs no evaluator: no FFL features, no script environments
        assertEquals(0, b.get("known_features").size());
        assertEquals(0, b.get("script_environments").size());
        assertEquals(6.5, b.get("resources").get("memory_gb").asDouble());
        assertEquals(5.0, b.get("wait_s").asDouble());
        assertTrue(b.get("kick").asBoolean());
    }

    @Test
    void noTaskIsEmpty() throws IOException {
        assertTrue(proxy().next("s", List.of("a.B"), List.of("a"), Map.of(), 0, false).isEmpty());
    }

    @Test
    void anErrorStatusThrowsSoTheCallerClaimsDirectly() {
        status.set(400);
        reply.set("{\"error\": \"server_id and claims are required\"}");
        assertThrows(IOException.class,
                () -> proxy().next("s", List.of("a.B"), List.of("a"), Map.of(), 0, false));
    }

    @Test
    void anUnreachableClaimerThrows() {
        ClaimProxy down = new ClaimProxy("http://127.0.0.1:9");  // discard: nothing listens
        assertThrows(IOException.class,
                () -> down.next("s", List.of("a.B"), List.of("a"), Map.of(), 0, false));
    }

    @Test
    void kickReachesTheClaimer() throws InterruptedException {
        proxy().kick();
        assertTrue(kicked.await(5, TimeUnit.SECONDS));
    }

    @Test
    void unsetEnvironmentMeansClaimDirectly() {
        if (System.getenv(ClaimProxy.ENV_URL) == null) {
            assertNull(ClaimProxy.fromEnvironment());
        }
    }
}
