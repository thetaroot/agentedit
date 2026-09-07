pub fn enqueue(item: &str) -> bool {
    !item.is_empty()
}

pub fn drain() -> Vec<String> {
    Vec::new()
}
