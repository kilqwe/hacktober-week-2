provider "aws" {
  region = "us-east-1"
}

resource "aws_instance" "backend_api" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t3.large"
}

resource "aws_db_instance" "primary_db" {
  engine            = "postgres"
  instance_class    = "db.r5.large"
  allocated_storage = 20
}

resource "aws_nat_gateway" "nat" {}
