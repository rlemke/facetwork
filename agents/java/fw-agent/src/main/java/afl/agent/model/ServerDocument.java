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

/**
 * Represents a server in the servers collection.
 */
public record ServerDocument(
        String uuid,
        String serverGroup,
        String serviceName,
        String serverName,
        List<String> serverIps,
        long startTime,
        long pingTime,
        List<String> topics,
        List<String> handlers,
        List<HandledStat> handled,
        String state
) {

    /**
     * Converts to a MongoDB document.
     */
    public Document toDocument() {
        Document doc = new Document()
                .append("uuid", uuid)
                .append("server_group", serverGroup)
                .append("service_name", serviceName)
                .append("server_name", serverName)
                .append("server_ips", serverIps)
                .append("start_time", startTime)
                .append("ping_time", pingTime)
                .append("topics", topics)
                .append("handlers", handlers)
                .append("state", state);

        if (handled != null) {
            doc.append("handled", handled.stream()
                    .map(HandledStat::toDocument)
                    .toList());
        }

        return doc;
    }

    /**
     * Represents handler statistics.
     */
    public record HandledStat(
            String handler,
            int handled,
            int notHandled
    ) {
        public Document toDocument() {
            return new Document()
                    .append("handler", handler)
                    .append("handled", handled)
                    .append("not_handled", notHandled);
        }
    }
}
