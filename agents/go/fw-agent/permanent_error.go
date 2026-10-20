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

import "errors"

// PermanentError is returned by a handler when retrying cannot change the
// outcome (bad input, a missing artifact that will not appear). The task is
// dead-lettered at once instead of spending its retry budget, and the runtime
// fails the step so catch blocks run.
type PermanentError struct {
	Msg string
	Err error
}

func (e *PermanentError) Error() string {
	if e.Err != nil && e.Msg == "" {
		return e.Err.Error()
	}
	if e.Err != nil {
		return e.Msg + ": " + e.Err.Error()
	}
	return e.Msg
}

func (e *PermanentError) Unwrap() error { return e.Err }

// NewPermanentError wraps a message as a PermanentError.
func NewPermanentError(msg string) error { return &PermanentError{Msg: msg} }

// IsPermanent reports whether err is, or wraps, a PermanentError.
func IsPermanent(err error) bool {
	var p *PermanentError
	return errors.As(err, &p)
}
