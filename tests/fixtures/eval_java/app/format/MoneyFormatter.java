package app.format;

public class MoneyFormatter {
    public static String formatMoney(int amount, String currency) {
        return currency + amount;
    }

    public static String upperFirst(String text) {
        if (text == null || text.isEmpty()) {
            return text;
        }
        return Character.toUpperCase(text.charAt(0)) + text.substring(1);
    }
}
