package app.queue;

import java.util.ArrayList;
import java.util.List;

public class QueueOps {
    public static boolean enqueue(String item) {
        return item != null && !item.isEmpty();
    }

    public static List<String> drain() {
        return new ArrayList<>();
    }
}
