group "all" {
  targets = ["myapp", "worker"]
}

target "myapp" {
  tags = ["registry.example.com/myapp:1.0"]
}

target "worker" {
  tags = ["registry.example.com/worker:1.0"]
}
