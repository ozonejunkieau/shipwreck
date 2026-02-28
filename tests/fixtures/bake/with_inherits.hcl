target "base" {
  dockerfile = "Dockerfile.base"
  tags = ["registry.example.com/base:1.0"]
}

target "myapp" {
  inherits = ["base"]
  tags = ["registry.example.com/myapp:1.0"]
}
