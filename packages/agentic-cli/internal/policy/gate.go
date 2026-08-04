package policy

func RequiresHumanGate(p Policy, gateName string) bool {
	gate, ok := p.Gates[gateName]
	return ok && gate.Required
}

func AuthorizationScopeForOperation(p Policy, operation string) (string, bool) {
	for name, scope := range p.AuthorizationScopes {
		for _, covered := range scope.CoveredOperations {
			if covered == operation {
				return name, true
			}
		}
	}
	return "", false
}
