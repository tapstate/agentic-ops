package jira

import "testing"

func TestNormalizeBaseURLUsesJiraCloudSiteRoot(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		want string
	}{
		{raw: "https://tapdata.atlassian.net/jira", want: "https://tapdata.atlassian.net"},
		{raw: "https://tapdata.atlassian.net/jira/", want: "https://tapdata.atlassian.net"},
		{raw: "https://tapdata.atlassian.net/", want: "https://tapdata.atlassian.net"},
	} {
		if got := NormalizeBaseURL(tc.raw); got != tc.want {
			t.Fatalf("NormalizeBaseURL(%q) = %q, want %q", tc.raw, got, tc.want)
		}
	}
}
