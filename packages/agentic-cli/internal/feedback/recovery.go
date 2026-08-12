package feedback

import (
	"crypto/sha256"
	"fmt"
	"strings"
)

func EvidenceSHA256(content []byte) string {
	return fmt.Sprintf("%x", sha256.Sum256(content))
}

func RecoveryFingerprint(event Event) string {
	if event.Recovery == nil {
		return ""
	}
	identity := strings.Join([]string{
		event.Workspace,
		event.AgenticRunID,
		event.Recovery.OriginalOperation,
		event.Recovery.OriginalCode,
		event.Recovery.EvidenceSHA256,
		event.Recovery.ExternalReference,
	}, "\x00")
	return fmt.Sprintf("%x", sha256.Sum256([]byte(identity)))
}

func AppendRecoveryEvent(path string, event Event) (fingerprint string, appended bool, err error) {
	fingerprint = RecoveryFingerprint(event)
	if fingerprint == "" {
		return "", false, fmt.Errorf("recovery record is required")
	}
	events, err := ReadEvents(path)
	if err != nil {
		return "", false, err
	}
	for _, existing := range events {
		if RecoveryFingerprint(existing) == fingerprint {
			return fingerprint, false, nil
		}
	}
	if err := AppendEvent(path, event); err != nil {
		return "", false, err
	}
	return fingerprint, true, nil
}
