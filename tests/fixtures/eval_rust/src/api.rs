use crate::format::upper_first;
use crate::service::enqueue_payment;
use crate::service::payment_summary;
use crate::service::process_payments;

pub fn handle_job_created(item: &str) -> bool {
    enqueue_payment(item)
}

pub fn handle_drain_request() -> u32 {
    let n = process_payments();
    let _ = upper_first(&n.to_string());
    n
}

pub fn build_receipt(total: u32) -> String {
    payment_summary(total)
}
