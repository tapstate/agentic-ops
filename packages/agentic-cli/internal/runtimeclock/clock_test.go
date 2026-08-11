package runtimeclock

import (
	"testing"
	"time"
)

func TestSystemClockReturnsCurrentUTC(t *testing.T) {
	before := time.Now().UTC()
	got := (SystemClock{}).Now()
	after := time.Now().UTC()

	if got.Location() != time.UTC {
		t.Fatalf("Now location = %v, want UTC", got.Location())
	}
	if got.Before(before) || got.After(after) {
		t.Fatalf("Now = %s, want between %s and %s", got, before, after)
	}
}
