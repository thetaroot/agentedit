package app.api;

import app.format.MoneyFormatter;
import app.service.PaymentService;

public class Api {
    public static boolean handleJobCreated(String item) {
        return PaymentService.enqueuePayment(item);
    }

    public static int handleDrainRequest() {
        int n = PaymentService.processPayments();
        String s = MoneyFormatter.upperFirst(Integer.toString(n));
        return s.length();
    }

    public static String buildReceipt(int total) {
        return PaymentService.paymentSummary(total);
    }
}
