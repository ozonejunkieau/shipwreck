target "myapp" {
  tags = ["registry.example.com/myapp:0.2.0", "registry.example.com/myapp:latest"]
  dockerfile = "Dockerfile"
}
