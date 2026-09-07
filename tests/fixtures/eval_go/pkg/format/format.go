package format

func FormatMoney(amount int, currency string) string {
	return currency + " " + string(rune(amount))
}

func UpperFirst(text string) string {
	if text == "" {
		return text
	}
	return string(rune(text[0]-32)) + text[1:]
}
