variable "REGISTRY" {
  default = "registry.example.com"
}

variable "VERSION" {
  default = "1.0.0"
}

group "default" {
  targets = ["myapp", "worker"]
}

target "myapp" {
  dockerfile = "Dockerfile"
  tags = [
    "${REGISTRY}/myapp:${VERSION}",
    "${REGISTRY}/myapp:latest",
  ]
  contexts = {
    base = "docker-image://registry.example.com/base/python:3.12"
  }
}

target "worker" {
  dockerfile = "Dockerfile.worker"
  tags = ["${REGISTRY}/worker:${VERSION}"]
}
