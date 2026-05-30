# =============================================================================
# All-in-One Linux VM — OpenD + collectors + BQ loaders + Query API
# =============================================================================

# --- Static external IP ---
resource "google_compute_address" "quant_vm" {
  name         = "quant-vm-ip"
  region       = var.region
  address_type = "EXTERNAL"
}

# --- Firewall: RDP ---
resource "google_compute_firewall" "quant_rdp" {
  name    = "quant-vm-rdp"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["3389"]
  }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["quant-vm"]
}

# --- Firewall: Query API ---
resource "google_compute_firewall" "quant_api" {
  name    = "quant-vm-api"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["quant-vm"]
}

# --- Ubuntu VM with MATE desktop + all services ---
resource "google_compute_instance" "quant_vm" {
  name         = "quant-vm"
  machine_type = "e2-standard-2"
  zone         = "${var.region}-a"
  tags         = ["quant-vm"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 50
      type  = "pd-ssd"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.quant_vm.address
    }
  }

  metadata_startup_script = file("${path.module}/startup.sh")

  service_account {
    scopes = ["cloud-platform"]
  }
}

# --- Outputs ---
output "quant_vm_ip" {
  description = "Public IP of quant VM"
  value       = google_compute_address.quant_vm.address
}

output "quant_vm_rdp_user" {
  description = "RDP username"
  value       = "quant"
}
