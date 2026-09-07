package app.service;

import app.format.MoneyFormatter;
import app.metrics.Metrics;
import app.queue.QueueOps;

import java.util.List;

public class PaymentService {
    public static boolean enqueuePayment(String item) {
        return QueueOps.enqueue(item);
    }

    public static int processPayments() {
        List<String> items = QueueOps.drain();
        int n = items.size();
        Metrics.record("processed", n);
        return n;
    }

    public static String paymentSummary(int total) {
        return MoneyFormatter.formatMoney(total, "EUR");
    }
}
