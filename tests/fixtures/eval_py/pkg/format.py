def format_money(amount: int, currency: str = "EUR") -> str:
    return f"{currency} {amount}"

def upper_first(text: str) -> str:
    return text.capitalize()
