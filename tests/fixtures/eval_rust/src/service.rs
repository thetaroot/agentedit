use crate::format::format_money;
use crate::metrics::record_metric;
use crate::queue::drain;
use crate::queue::enqueue;

pub fn enqueue_payment(item: &str) -> bool {
    enqueue(item)
}

pub fn process_payments() -> u32 {
    let items = drain();
    let n = items.len() as u32;
    record_metric("processed", n);
    n
}

pub fn payment_summary(total: u32) -> String {
    format_money(total, "EUR")
}
