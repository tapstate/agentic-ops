package runtimeconfig

import (
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/localenv"

	"gopkg.in/yaml.v3"
)

type FieldSpec struct {
	Key      string
	EnvName  string
	Default  string
	Prompt   string
	Target   string
	Secret   bool
	Required bool
}

func (field FieldSpec) FullKey(module string) string {
	key := strings.TrimSpace(field.Key)
	if key == "" {
		return ""
	}
	if strings.Contains(key, ".") {
		return key
	}
	module = strings.TrimSpace(module)
	if module == "" {
		return key
	}
	return module + "." + key
}

type ModuleSpec struct {
	Name   string
	Fields []FieldSpec
}

type Registry struct {
	modules map[string]ModuleSpec
}

func NewRegistry() *Registry {
	return &Registry{modules: map[string]ModuleSpec{}}
}

func (registry *Registry) Register(spec ModuleSpec) {
	if registry.modules == nil {
		registry.modules = map[string]ModuleSpec{}
	}
	registry.modules[spec.Name] = spec
}

func (registry *Registry) Get(module string) (ModuleSpec, bool) {
	spec, ok := registry.modules[module]
	return spec, ok
}

func (registry *Registry) FindField(key string) (ModuleSpec, FieldSpec, bool) {
	key = strings.TrimSpace(key)
	if key == "" {
		return ModuleSpec{}, FieldSpec{}, false
	}
	for _, module := range registry.modules {
		for _, field := range module.Fields {
			if field.FullKey(module.Name) == key {
				return module, field, true
			}
		}
	}
	return ModuleSpec{}, FieldSpec{}, false
}

type Scope struct {
	InstallDir    string
	WorkspaceRoot string
	Project       string
}

func NewScope(installDir string, workspaceRoot string, project string) Scope {
	return Scope{
		InstallDir:    strings.TrimSpace(installDir),
		WorkspaceRoot: strings.TrimSpace(workspaceRoot),
		Project:       strings.TrimSpace(project),
	}
}

func (scope Scope) UserConfigPath() string {
	return filepath.Join(scope.InstallDir, "user", "config.local.yaml")
}

func (scope Scope) UserEnvPath() string {
	return filepath.Join(scope.InstallDir, "user", ".env")
}

func (scope Scope) WorkspaceConfigPath() string {
	return filepath.Join(scope.WorkspaceRoot, ".agentic-ops", "config.local.yaml")
}

func (scope Scope) WorkspaceEnvPath() string {
	return filepath.Join(scope.WorkspaceRoot, ".agentic-ops", ".env")
}

func (scope Scope) ConfigPaths() []string {
	paths := []string{}
	if scope.WorkspaceRoot != "" {
		paths = append(paths, scope.WorkspaceConfigPath())
	}
	paths = append(paths, scope.UserConfigPath())
	return paths
}

func (scope Scope) EnvPaths() []string {
	paths := []string{}
	if scope.WorkspaceRoot != "" {
		paths = append(paths, scope.WorkspaceEnvPath())
	}
	paths = append(paths, scope.UserEnvPath())
	return paths
}

func (scope Scope) LookupEnv(key string) (string, bool, error) {
	key = strings.TrimSpace(key)
	if key == "" {
		return "", false, nil
	}
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value, true, nil
	}
	return LookupEnvFiles(scope.EnvPaths(), key)
}

func LookupEnvFiles(paths []string, key string) (string, bool, error) {
	key = strings.TrimSpace(key)
	if key == "" {
		return "", false, nil
	}
	for _, path := range paths {
		value, ok, err := localenv.Lookup(path, key)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return "", false, err
		}
		if ok {
			return value, true, nil
		}
	}
	return "", false, nil
}

func (scope Scope) EnsureUserEnvPlaceholder(key string, comment string) error {
	return EnsureEnvPlaceholder(scope.UserEnvPath(), key, comment)
}

func (scope Scope) WriteUserEnvValue(key string, value string, comment string) error {
	return WriteEnvValue(scope.UserEnvPath(), key, value, comment)
}

func EnsureEnvPlaceholder(path string, key string, comment string) error {
	key = strings.TrimSpace(key)
	if key == "" {
		return nil
	}
	if values, err := localenv.LoadFile(path); err == nil {
		if _, ok := values[key]; ok {
			return nil
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()

	if stat, err := file.Stat(); err == nil && stat.Size() > 0 {
		if _, err := file.WriteString("\n"); err != nil {
			return err
		}
	}
	if comment = strings.TrimSpace(comment); comment != "" {
		if _, err := file.WriteString("# " + comment + "\n"); err != nil {
			return err
		}
	}
	_, err = file.WriteString(key + "=\n")
	return err
}

func WriteEnvValue(path string, key string, value string, comment string) error {
	key = strings.TrimSpace(key)
	value = strings.TrimSpace(value)
	if key == "" || value == "" {
		return nil
	}
	lines := []string{}
	found := false
	if data, err := os.ReadFile(path); err == nil {
		for _, line := range strings.Split(strings.TrimRight(string(data), "\n"), "\n") {
			trimmed := strings.TrimSpace(line)
			candidate := strings.TrimPrefix(trimmed, "export ")
			name, _, ok := strings.Cut(candidate, "=")
			if ok && strings.TrimSpace(name) == key {
				if !found {
					lines = append(lines, key+"="+value)
					found = true
				}
				continue
			}
			lines = append(lines, line)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if !found {
		if comment = strings.TrimSpace(comment); comment != "" {
			lines = append(lines, "# "+comment)
		}
		lines = append(lines, key+"="+value)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(strings.TrimRight(strings.Join(lines, "\n"), "\n")+"\n"), 0o600)
}

func ResolveProjectModule(scope Scope, module string, out any) (string, bool, error) {
	for _, path := range scope.ConfigPaths() {
		used, err := ReadProjectModule(path, scope.Project, module, out)
		if err != nil {
			return "", false, err
		}
		if used {
			return path, true, nil
		}
	}
	return "", false, nil
}

func ReadProjectModule(path string, project string, module string, out any) (bool, error) {
	document, used, err := readDocument(path)
	if err != nil || !used {
		return false, err
	}
	merged := map[string]any{}
	if globalModule, ok := mapValue(document[module]); ok {
		mergeMap(merged, globalModule)
	}
	if projects, ok := mapValue(document["projects"]); ok {
		if projectConfig, ok := mapValue(projects[project]); ok {
			if projectModule, ok := mapValue(projectConfig[module]); ok {
				mergeMap(merged, projectModule)
			}
		}
	}
	if len(merged) == 0 {
		return false, nil
	}
	data, err := yaml.Marshal(merged)
	if err != nil {
		return false, err
	}
	if err := yaml.Unmarshal(data, out); err != nil {
		return false, err
	}
	return true, nil
}

func WriteProjectModule(path string, project string, module string, value any) error {
	document, _, err := readDocument(path)
	if err != nil {
		return err
	}
	if document == nil {
		document = map[string]any{}
	}
	projects, ok := mapValue(document["projects"])
	if !ok {
		projects = map[string]any{}
		document["projects"] = projects
	}
	projectConfig, ok := mapValue(projects[project])
	if !ok {
		projectConfig = map[string]any{}
		projects[project] = projectConfig
	}
	moduleValue, err := structToMap(value)
	if err != nil {
		return err
	}
	projectConfig[module] = moduleValue
	data, err := yaml.Marshal(document)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o600)
}

func readDocument(path string) (map[string]any, bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return map[string]any{}, false, nil
		}
		return nil, false, err
	}
	if strings.TrimSpace(string(data)) == "" {
		return map[string]any{}, false, nil
	}
	var document map[string]any
	if err := yaml.Unmarshal(data, &document); err != nil {
		return nil, false, err
	}
	if document == nil {
		document = map[string]any{}
	}
	return document, true, nil
}

func structToMap(value any) (map[string]any, error) {
	data, err := yaml.Marshal(value)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := yaml.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	if result == nil {
		result = map[string]any{}
	}
	return result, nil
}

func mapValue(value any) (map[string]any, bool) {
	switch typed := value.(type) {
	case map[string]any:
		return typed, true
	case map[any]any:
		result := map[string]any{}
		for key, value := range typed {
			keyString, ok := key.(string)
			if !ok {
				continue
			}
			result[keyString] = value
		}
		return result, true
	default:
		return nil, false
	}
}

func mergeMap(target map[string]any, source map[string]any) {
	for key, value := range source {
		if strings.TrimSpace(key) == "" {
			continue
		}
		target[key] = value
	}
}
