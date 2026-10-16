terraform {
  required_version = ">= 1.6"
  required_providers {
    scaleway = { source = "scaleway/scaleway", version = "~> 2.40" }
  }
}

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "fr-par"
}
variable "zone" {
  type    = string
  default = "fr-par-1"
}
variable "name" {
  type    = string
  default = "canot"
}
variable "with_vm" {
  description = "Créer aussi la VM (catalogue Lakekeeper + worker). Faux = seulement les deux buckets."
  type        = bool
  default     = false
}
variable "vm_type" {
  type    = string
  default = "DEV1-L"
}
variable "ssh_public_key" {
  type    = string
  default = ""
}

provider "scaleway" {
  region     = var.region
  zone       = var.zone
  project_id = var.project_id
}

# Données : versionné, pour garder les anciennes versions de chaque fichier.
resource "scaleway_object_bucket" "data" {
  name = "${var.name}-data-${var.project_id}"
  versioning { enabled = true }
  tags = { app = "canot", role = "data" }
}

# Preuves : verrouillé (object lock), on peut écrire mais personne ne modifie avant la fin de la rétention.
resource "scaleway_object_bucket" "evidence" {
  name               = "${var.name}-evidence-${var.project_id}"
  object_lock_enabled = true
  tags               = { app = "canot", role = "evidence" }
}

resource "scaleway_vpc_private_network" "pn" {
  count = var.with_vm ? 1 : 0
  name  = "${var.name}-pn"
}

resource "scaleway_instance_ip" "ip" {
  count = var.with_vm ? 1 : 0
}

resource "scaleway_instance_security_group" "sg" {
  count                   = var.with_vm ? 1 : 0
  name                    = "${var.name}-sg"
  inbound_default_policy  = "drop"
  outbound_default_policy = "accept"
}

resource "scaleway_instance_server" "vm" {
  count             = var.with_vm ? 1 : 0
  name              = "${var.name}-worker"
  type              = var.vm_type
  image             = "ubuntu_jammy"
  ip_id             = scaleway_instance_ip.ip[0].id
  security_group_id = scaleway_instance_security_group.sg[0].id
  private_network { pn_id = scaleway_vpc_private_network.pn[0].id }
  user_data = {
    cloud-init = templatefile("${path.module}/cloud-init.yaml", {
      data_bucket = scaleway_object_bucket.data.name
      region      = var.region
    })
  }
  tags = ["canot"]
}

output "data_bucket" { value = scaleway_object_bucket.data.name }
output "evidence_bucket" { value = scaleway_object_bucket.evidence.name }
output "endpoint" { value = "https://s3.${var.region}.scw.cloud" }
output "drill_command" {
  value = "canot drill --from <source> --to scaleway:${var.region} --bucket ${scaleway_object_bucket.data.name}"
}
