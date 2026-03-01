// Docker Bake build definition for a Python microservices stack.
//
// Demonstrates:
//   - HCL variables with defaults (resolved by Shipwreck)
//   - Target groups
//   - Target inheritance
//   - docker-image:// contexts (builds_from edges)
//   - Multi-tag targets (produces edges)

variable "REGISTRY" {
  default = "registry.example.com"
}

variable "VERSION" {
  default = "3.8.1"
}

variable "PYTHON_VERSION" {
  default = "3.12"
}

// --------------------------------------------------------------------------
// Groups
// --------------------------------------------------------------------------

group "default" {
  targets = ["api", "worker"]
}

group "all" {
  targets = ["base", "api", "worker", "migrate"]
}

// --------------------------------------------------------------------------
// Targets
// --------------------------------------------------------------------------

// Internal base image — built from an external Python image.
target "base" {
  dockerfile = "Dockerfile.base"
  contexts = {
    upstream = "docker-image://docker.io/library/python:${PYTHON_VERSION}-slim"
  }
  tags = [
    "${REGISTRY}/base/python:${PYTHON_VERSION}",
    "${REGISTRY}/base/python:latest",
  ]
}

// API service — inherits from base, adds application code.
target "api" {
  inherits   = ["base"]
  dockerfile = "Dockerfile"
  contexts = {
    base = "docker-image://${REGISTRY}/base/python:${PYTHON_VERSION}"
  }
  tags = [
    "${REGISTRY}/apps/api:${VERSION}",
    "${REGISTRY}/apps/api:latest",
  ]
  args = {
    APP_PORT = "8000"
  }
}

// Background worker — shares the same base image as the API.
target "worker" {
  inherits   = ["base"]
  dockerfile = "Dockerfile.worker"
  contexts = {
    base = "docker-image://${REGISTRY}/base/python:${PYTHON_VERSION}"
  }
  tags = [
    "${REGISTRY}/apps/worker:${VERSION}",
    "${REGISTRY}/apps/worker:latest",
  ]
}

// Database migration runner — one-shot job image.
target "migrate" {
  dockerfile = "Dockerfile.migrate"
  contexts = {
    base = "docker-image://${REGISTRY}/base/python:${PYTHON_VERSION}"
  }
  tags = [
    "${REGISTRY}/apps/migrate:${VERSION}",
  ]
}
