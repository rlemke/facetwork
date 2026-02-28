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

import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import org.bson.Document;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * RegistryRunner wraps an AgentPoller and restricts polling to only those
 * handler names that also appear in MongoDB's handler_registrations collection.
 * This provides DB-driven topic filtering without requiring dynamic module loading.
 */
public class RegistryRunner implements AutoCloseable {

    private static final Logger logger = Logger.getLogger(RegistryRunner.class.getName());

    private final AgentPoller poller;
    private final long refreshIntervalMs;
    private final Set<String> activeTopics = ConcurrentHashMap.newKeySet();
    private ScheduledExecutorService refreshScheduler;

    public RegistryRunner(AgentPollerConfig config) {
        this(config, 30000);
    }

    public RegistryRunner(AgentPollerConfig config, long refreshIntervalMs) {
        this.poller = new AgentPoller(config);
        this.refreshIntervalMs = refreshIntervalMs;
    }

    /**
     * Registers a handler (delegates to the underlying poller).
     */
    public void register(String facetName, Handler handler) {
        poller.register(facetName, handler);
    }

    /**
     * Returns all registered handler names from the underlying poller.
     */
    public List<String> registeredHandlers() {
        return poller.registeredHandlers();
    }

    /**
     * Returns the effective handlers: intersection of registered and active topics.
     */
    public List<String> effectiveHandlers() {
        List<String> registered = poller.registeredHandlers();
        List<String> result = new ArrayList<>();
        for (String name : registered) {
            if (activeTopics.contains(name)) {
                result.add(name);
            }
        }
        return result;
    }

    /**
     * Refreshes active topics from the handler_registrations collection.
     */
    public void refreshTopics(MongoDatabase db) {
        try {
            MongoCollection<Document> coll = db.getCollection(
                    Protocol.COLLECTION_HANDLER_REGISTRATIONS);
            Set<String> topics = new HashSet<>();
            for (Document doc : coll.find()) {
                String name = doc.getString("facet_name");
                if (name != null) {
                    topics.add(name);
                }
            }
            activeTopics.clear();
            activeTopics.addAll(topics);
            logger.fine("Refreshed " + topics.size() + " active topics from DB");
        } catch (Exception e) {
            logger.log(Level.WARNING, "Failed to refresh topics", e);
        }
    }

    /**
     * Starts the runner. Delegates to the underlying poller.
     */
    public void start() throws InterruptedException {
        poller.start();
    }

    /**
     * Stops the runner and cleans up.
     */
    public void stop() {
        if (refreshScheduler != null) {
            refreshScheduler.shutdown();
            try {
                refreshScheduler.awaitTermination(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        poller.stop();
    }

    @Override
    public void close() {
        stop();
    }
}
