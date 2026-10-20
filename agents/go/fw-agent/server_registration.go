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

package fwagent

import (
	"context"
	"net"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

// ServerRegistration handles server lifecycle in MongoDB.
type ServerRegistration struct {
	db *mongo.Database
}

// NewServerRegistration creates a new ServerRegistration instance.
func NewServerRegistration(db *mongo.Database) *ServerRegistration {
	return &ServerRegistration{db: db}
}

// Register registers a server in the servers collection.
func (s *ServerRegistration) Register(ctx context.Context, serverID string, cfg Config, handlers []string) error {
	collection := s.db.Collection(CollectionServers)

	now := NowMillis()
	server := ServerDocument{
		UUID:        serverID,
		ServerGroup: cfg.ServerGroup,
		ServiceName: cfg.ServiceName,
		ServerName:  cfg.ServerName,
		ServerIPs:   getLocalIPs(),
		StartTime:   now,
		PingTime:    now,
		Topics:      handlers,
		Handlers:    handlers,
		Handled:     nil,
		State:       ServerStateRunning,

		TaskList:             cfg.TaskList,
		Resources:            Capacity(),
		ProvidedEnvironments: []string{},
		ASTFeatures:          []string{},
	}

	opts := options.Update().SetUpsert(true)
	_, err := collection.UpdateOne(
		ctx,
		bson.M{"uuid": serverID},
		bson.M{"$set": server},
		opts,
	)
	return err
}

// Deregister marks a server as shutdown.
func (s *ServerRegistration) Deregister(ctx context.Context, serverID string) error {
	collection := s.db.Collection(CollectionServers)

	update := bson.M{
		"$set": bson.M{
			"state":     ServerStateShutdown,
			"ping_time": NowMillis(),
		},
	}

	_, err := collection.UpdateOne(ctx, bson.M{"uuid": serverID}, update)
	return err
}

// Heartbeat pings the server record; see Ping.
//
// Deprecated: discards whether the record was live. Use Ping and re-register
// when it reports false.
func (s *ServerRegistration) Heartbeat(ctx context.Context, serverID string) error {
	_, err := s.Ping(ctx, serverID)
	return err
}

// Ping updates the record's ping time (protocol heartbeat_server) and
// publishes the memory available now as free_resources. It matches only a
// non-terminal record, so a quarantined server keeps pinging without becoming
// running again. false means the record is gone or marked shutdown (a reaper
// false positive): the caller must Register again.
func (s *ServerRegistration) Ping(ctx context.Context, serverID string) (bool, error) {
	now := NowMillis()
	set := bson.M{"ping_time": now}
	if free := LiveFree(); free != nil {
		free["at"] = now
		set["free_resources"] = free
	}
	res, err := s.db.Collection(CollectionServers).UpdateOne(ctx,
		bson.M{"uuid": serverID, "state": bson.M{"$nin": bson.A{ServerStateShutdown}}},
		bson.M{"$set": set})
	if err != nil {
		return false, err
	}
	return res.MatchedCount > 0, nil
}

func getLocalIPs() []string {
	var ips []string
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return ips
	}

	for _, addr := range addrs {
		if ipnet, ok := addr.(*net.IPNet); ok && !ipnet.IP.IsLoopback() {
			if ipnet.IP.To4() != nil {
				ips = append(ips, ipnet.IP.String())
			}
		}
	}
	return ips
}
