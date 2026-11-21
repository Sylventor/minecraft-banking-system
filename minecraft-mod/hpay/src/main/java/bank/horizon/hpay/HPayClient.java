package bank.horizon.hpay;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.keybinding.v1.KeyBindingHelper;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.block.entity.SignBlockEntity;
import net.minecraft.block.entity.SignText;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.input.KeyInput;
import net.minecraft.client.input.MouseInput;
import net.minecraft.client.option.KeyBinding;
import net.minecraft.client.util.InputUtil;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.BlockHitResult;
import org.lwjgl.glfw.GLFW;
import org.slf4j.LoggerFactory;

public final class HPayClient implements ClientModInitializer {
    private static final Pattern PRODUCT = Pattern.compile(
            "(?<![\\p{L}\\p{N}_-])H-([1-9][0-9]{0,18})(?![\\p{L}\\p{N}_-])");
    private static final String MAX_ID = Long.toString(Long.MAX_VALUE);
    private static final Gson JSON = new GsonBuilder().setPrettyPrinting().create();
    private static KeyBinding binding;
    private static Settings settings;
    private static long lastSend;
    private static boolean sent;

    private record Settings(String atmNickname, boolean notifications, int minimumIntervalMs) {
        static Settings load(Path path) throws IOException {
            if (Files.notExists(path)) {
                var defaults = new Settings("BankingATM", true, 250);
                Files.createDirectories(path.getParent());
                Files.writeString(path, JSON.toJson(defaults));
                return defaults;
            }
            var result = JSON.fromJson(Files.readString(path), Settings.class);
            if (result == null || result.atmNickname == null ||
                    !result.atmNickname.matches("[A-Za-z0-9_]{3,16}") ||
                    result.minimumIntervalMs < 250 || result.minimumIntervalMs > 5000) {
                throw new IllegalArgumentException("Invalid HPay nickname or interval");
            }
            return result;
        }
    }

    @Override
    public void onInitializeClient() {
        binding = KeyBindingHelper.registerKeyBinding(new KeyBinding(
                "key.hpay.pay", InputUtil.Type.KEYSYM, GLFW.GLFW_KEY_J, KeyBinding.Category.create(Identifier.of("hpay", "payments"))));
        try {
            settings = Settings.load(FabricLoader.getInstance().getConfigDir().resolve("hpay.json"));
        } catch (IOException | RuntimeException error) {
            LoggerFactory.getLogger("HPay").warn("Cannot load config/hpay.json", error);
        }
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> sent = false);
    }

    public static void keyboard(long window, int action, KeyInput input) {
        // GLFW emits PRESS for an edge and REPEAT for a held key.
        if (action == GLFW.GLFW_PRESS && binding != null && binding.matchesKey(input)) {
            press(window);
        }
    }

    public static void mouse(long window, MouseInput input, int action) {
        if (action == GLFW.GLFW_PRESS && binding != null &&
                KeyBindingHelper.getBoundKeyOf(binding).equals(InputUtil.Type.MOUSE.createFromCode(input.button()))) {
            press(window);
        }
    }

    private static void press(long window) {
        var client = MinecraftClient.getInstance();
        if (client.getWindow().getHandle() != window || !client.isWindowFocused() ||
                client.currentScreen != null || client.world == null || client.player == null ||
                client.getNetworkHandler() == null) return;
        if (settings == null) {
            notifyPlayer(client, "hpay.invalid_config");
            return;
        }
        long now = System.nanoTime();
        if (sent && now - lastSend < TimeUnit.MILLISECONDS.toNanos(settings.minimumIntervalMs)) return;
        if (!(client.crosshairTarget instanceof BlockHitResult hit) ||
                hit.getType() != net.minecraft.util.hit.HitResult.Type.BLOCK ||
                !(client.world.getBlockEntity(hit.getBlockPos()) instanceof SignBlockEntity sign)) {
            notifyPlayer(client, "hpay.no_sign");
            return;
        }
        Set<String> products = new HashSet<>();
        collect(sign.getFrontText(), products);
        collect(sign.getBackText(), products);
        if (products.size() != 1) {
            notifyPlayer(client, products.isEmpty() ? "hpay.no_product" : "hpay.ambiguous");
            return;
        }
        var product = products.iterator().next();
        client.getNetworkHandler().sendChatCommand("msg " + settings.atmNickname + " $pay " + product);
        lastSend = now;
        sent = true;
    }

    private static void collect(SignText side, Set<String> products) {
        for (Text line : side.getMessages(false)) {
            var match = PRODUCT.matcher(line.getString());
            while (match.find()) {
                var value = match.group(1);
                if (value.length() < MAX_ID.length() || value.compareTo(MAX_ID) <= 0) products.add(value);
            }
        }
    }

    private static void notifyPlayer(MinecraftClient client, String key) {
        if (settings == null || settings.notifications) client.player.sendMessage(Text.translatable(key), true);
    }
}
