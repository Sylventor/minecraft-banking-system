package bank.horizon.hpay.mixin;

import bank.horizon.hpay.HPayClient;
import net.minecraft.client.Mouse;
import net.minecraft.client.input.MouseInput;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Mouse.class)
abstract class MouseMixin {
    @Inject(method = "onMouseButton", at = @At("HEAD"))
    private void hpayPress(long window, MouseInput input, int action, CallbackInfo ci) {
        HPayClient.mouse(window, input, action);
    }
}
