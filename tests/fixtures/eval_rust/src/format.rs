pub fn format_money(amount: u32, currency: &str) -> String {
    format!("{}{}", currency, amount)
}

pub fn upper_first(text: &str) -> String {
    let mut out = String::new();
    let mut first = true;
    for ch in text.chars() {
        if first {
            out.extend(ch.to_uppercase());
            first = false;
        } else {
            out.push(ch);
        }
    }
    out
}
