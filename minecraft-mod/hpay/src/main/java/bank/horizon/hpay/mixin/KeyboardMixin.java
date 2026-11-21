package bank.horizon.hpay.mixin;

import bank.horizon.hpay.HPayClient;
import net.minecraft.client.Keyboard;
import net.minecraft.client.input.KeyInput;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Keyboard.class)
abstract class KeyboardMixin {
    @Inject(method = "onKey", at = @At("HEAD"))
    private void hpayPress(long window, int action, KeyInput input, CallbackInfo ci) {
        HPayClient.keyboard(window, action, input);
    }
}
