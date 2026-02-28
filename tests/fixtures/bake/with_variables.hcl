variable "REGISTRY" {
  default = "registry.example.com"
}
variable "VERSION" {
  default = "0.2.0"
}

target "myapp" {
  tags = [
    "${REGISTRY}/myapp:${VERSION}",
    "${REGISTRY}/myapp:latest",
  ]
  dockerfile = "Dockerfile"
}
