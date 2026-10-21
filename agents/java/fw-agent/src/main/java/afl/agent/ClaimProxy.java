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

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Client for the host's claimer ({@code facetwork.runtime.claimer}).
 *
 * <p>The claimer polls MongoDB once for every runner on its machine and hands
 * each task over with an ownership-gated write, so a task it returns is already
 * {@code running} under this agent's server id. The agent sends the same spec
 * its direct claim uses: its handler names and task list, the memory free now,
 * and no FFL features or script environments (an agent declines both).
 */
public final class ClaimProxy {

    /** Env var naming the claimer, e.g. {@code http://runner-claimer:8790}. */
    public static final String ENV_URL = "FW_CLAIM_PROXY_URL";

    private static final ObjectMapper JSON = new ObjectMapper();

    private final String baseUrl;
    private final HttpClient http;

    public ClaimProxy(String baseUrl) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    }

    /** The claimer named by {@link #ENV_URL}, or null when it is unset. */
    public static ClaimProxy fromEnvironment() {
        String url = System.getenv(ENV_URL);
        return url == null || url.isBlank() ? null : new ClaimProxy(url.trim());
    }

    public String baseUrl() {
        return baseUrl;
    }

    /**
     * Asks for one task, waiting up to {@code waitS} seconds for one to appear.
     *
     * @param kick work just finished here, so follow-on tasks may be waiting:
     *             the claimer should query now instead of after its idle backoff
     * @return the uuid of a task now held by {@code serverId}, or empty
     * @throws IOException when the claimer cannot be reached or answers badly;
     *                     the caller claims directly instead
     */
    public Optional<String> next(String serverId, List<String> taskNames, List<String> taskLists,
                                 Map<String, Object> resources, double waitS, boolean kick)
            throws IOException {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("server_id", serverId);
        body.put("claims", List.of(List.of(taskNames, taskLists)));
        body.put("known_features", List.of());
        body.put("resources", resources == null ? Map.of() : resources);
        body.put("script_environments", List.of());
        body.put("wait_s", waitS);
        body.put("kick", kick);
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + "/next"))
                .timeout(Duration.ofMillis((long) ((waitS + 30) * 1000)))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(JSON.writeValueAsBytes(body)))
                .build();
        HttpResponse<String> resp;
        try {
            resp = http.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted waiting for the claimer", e);
        }
        if (resp.statusCode() != 200) {
            throw new IOException("claimer answered HTTP " + resp.statusCode() + ": " + resp.body());
        }
        JsonNode task = JSON.readTree(resp.body()).get("task");
        return task == null || task.isNull() || task.asText().isEmpty()
                ? Optional.empty() : Optional.of(task.asText());
    }

    /** Tells the claimer work may be waiting. Fire-and-forget: a lost kick costs latency only. */
    public void kick() {
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + "/kick"))
                .timeout(Duration.ofSeconds(2))
                .POST(HttpRequest.BodyPublishers.ofString("{}"))
                .build();
        http.sendAsync(req, HttpResponse.BodyHandlers.discarding()).exceptionally(e -> null);
    }
}
