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

/**
 * Thrown by a handler when retrying cannot change the outcome (bad input, a
 * missing artifact that will not appear). The task is dead-lettered at once
 * instead of spending its retry budget, and the step fails so catch blocks run.
 */
public class PermanentError extends RuntimeException {

    public PermanentError(String message) {
        super(message);
    }

    public PermanentError(String message, Throwable cause) {
        super(message, cause);
    }
}
