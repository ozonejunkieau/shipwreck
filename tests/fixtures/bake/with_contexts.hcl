variable "REGISTRY" {
  default = "registry.example.com"
}

target "myapp" {
  contexts = {
    base      = "docker-image://registry.example.com/base/python:3.12"
    local_ctx = "."
  }
  tags = ["${REGISTRY}/myapp:1.0"]
}
