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

import afl.agent.model.ServerDocument;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.UpdateOptions;
import com.mongodb.client.model.Updates;
import org.bson.Document;

import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;

import static com.mongodb.client.model.Filters.eq;

/**
 * Handles server lifecycle in MongoDB.
 */
public class ServerRegistration {

    private final MongoDatabase db;

    public ServerRegistration(MongoDatabase db) {
        this.db = db;
    }

    /**
     * Registers a server in the servers collection.
     */
    public void register(String serverId, AgentPollerConfig config, List<String> handlers) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_SERVERS);

        long now = MongoOps.nowMillis();
        ServerDocument server = new ServerDocument(
                serverId,
                config.serverGroup(),
                config.serviceName(),
                config.serverName(),
                getLocalIPs(),
                now,
                now,
                handlers,
                handlers,
                null,
                Protocol.SERVER_STATE_RUNNING
        );

        collection.updateOne(
                eq("uuid", serverId),
                new Document("$set", server.toDocument()),
                new UpdateOptions().upsert(true)
        );
    }

    /**
     * Marks a server as shutdown.
     */
    public void deregister(String serverId) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_SERVERS);

        collection.updateOne(
                eq("uuid", serverId),
                Updates.combine(
                        Updates.set("state", Protocol.SERVER_STATE_SHUTDOWN),
                        Updates.set("ping_time", MongoOps.nowMillis())
                )
        );
    }

    /**
     * Updates the server's ping time.
     */
    public void heartbeat(String serverId) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_SERVERS);

        collection.updateOne(
                eq("uuid", serverId),
                Updates.set("ping_time", MongoOps.nowMillis())
        );
    }

    /**
     * Returns local non-loopback IPv4 addresses.
     */
    private static List<String> getLocalIPs() {
        List<String> ips = new ArrayList<>();

        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                NetworkInterface iface = interfaces.nextElement();
                if (iface.isLoopback() || !iface.isUp()) {
                    continue;
                }

                Enumeration<InetAddress> addresses = iface.getInetAddresses();
                while (addresses.hasMoreElements()) {
                    InetAddress addr = addresses.nextElement();
                    if (addr.isLoopbackAddress()) {
                        continue;
                    }
                    // Only IPv4
                    String hostAddress = addr.getHostAddress();
                    if (!hostAddress.contains(":")) {
                        ips.add(hostAddress);
                    }
                }
            }
        } catch (Exception e) {
            // Ignore errors getting network interfaces
        }

        return ips;
    }
}
