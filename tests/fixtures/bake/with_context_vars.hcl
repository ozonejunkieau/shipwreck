variable "REGISTRY" {
  default = "registry.example.com"
}

variable "PY_VERSION" {
  default = "3.12"
}

target "myapp" {
  contexts = {
    base = "docker-image://${REGISTRY}/base/python:${PY_VERSION}"
  }
  tags = ["${REGISTRY}/myapp:1.0"]
}
