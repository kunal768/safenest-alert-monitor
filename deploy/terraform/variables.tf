variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "enable_cloud_deploy" {
  type    = bool
  default = false
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}
