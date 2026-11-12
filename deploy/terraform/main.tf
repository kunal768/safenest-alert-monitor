terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  n = var.enable_cloud_deploy ? 1 : 0
}

resource "aws_security_group" "safenest" {
  count       = local.n
  name        = "safenest-demo-sg"
  description = "SafeNest demo ALB + instance (not applied unless ENABLE_CLOUD_DEPLOY)"

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "api" {
  count                  = local.n
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.safenest[0].id]
  user_data              = <<-EOF
    #!/bin/bash
    echo "SafeNest placeholder — Ollama would need a GPU/CPU host"
  EOF
  tags = { Name = "safenest-api" }
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

resource "aws_lb" "api" {
  count              = local.n
  name               = "safenest-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.safenest[0].id]
  subnets            = data.aws_subnets.default.ids
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_lb_target_group" "api" {
  count    = local.n
  name     = "safenest-tg"
  port     = 8000
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id
  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_target_group_attachment" "api" {
  count            = local.n
  target_group_arn = aws_lb_target_group.api[0].arn
  target_id        = aws_instance.api[0].id
  port             = 8000
}

resource "aws_lb_listener" "http" {
  count             = local.n
  load_balancer_arn = aws_lb.api[0].arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}

output "alb_dns" {
  value = try(aws_lb.api[0].dns_name, "disabled by feature flag")
}
