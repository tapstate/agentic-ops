package policy

func RequiresHumanGate(p Policy, gateName string) bool {
	gate, ok := p.Gates[gateName]
	return ok && gate.Required
}
