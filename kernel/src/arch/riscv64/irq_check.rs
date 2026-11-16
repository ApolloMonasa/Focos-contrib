//! QEMU 启动自检：核对初态开/关与单层/两层闭包的中断状态和返回值。

use super::irq::{local_irqs_enabled, set_local_irqs_enabled, with_local_irqs_disabled};

/// 本核中断状态自检失败的原因。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IrqCheckError {
    /// 进入自检时 SIE 应为 0；启动汇编已经关闭中断。
    InitialStateEnabled,
    /// 闭包执行次数不符合预期。
    CallCount {
        scenario: &'static str,
        expected: u32,
        actual: u32,
    },
    /// 闭包返回值没有原样透传。
    ReturnValue {
        scenario: &'static str,
        expected: u32,
        actual: u32,
    },
    /// 某处读到的 SIE 与预期不一致。
    State {
        scenario: &'static str,
        expected: bool,
        actual: bool,
    },
}

/// 依次验证初态开/关与单层/两层闭包；无论成功或失败，返回时都保持关闭中断。
///
/// 自检只切换 `sstatus.SIE`，不接入定时器或外部中断源，因此不能替代
/// ARCH-001 的待处理定时中断验收。
pub fn check_local_irq_state() -> Result<(), IrqCheckError> {
    if local_irqs_enabled() {
        // SAFETY: 只清除 SIE；返回前恢复本函数承诺的关闭中断状态。
        unsafe { set_local_irqs_enabled(false) };
        return Err(IrqCheckError::InitialStateEnabled);
    }

    check_single(false)?;
    check_single(true)?;
    check_nested(false)?;
    check_nested(true)?;

    expect_state("final", false)
}

/// 验证单层闭包只执行一次、返回原值，并在退出后恢复进入前的 SIE。
fn check_single(initial: bool) -> Result<(), IrqCheckError> {
    let scenario = if initial { "single-on" } else { "single-off" };
    // SAFETY: 启动流程尚未设置 sie，切换 SIE 不会触发中断；自检在返回前恢复关闭。
    unsafe { set_local_irqs_enabled(initial) };
    let result = (|| {
        expect_state(scenario, initial)?;

        let mut calls = 0u32;
        let mut inside = !initial;
        let value = with_local_irqs_disabled(|| {
            calls += 1;
            inside = local_irqs_enabled();
            0x35u32
        });

        expect_state(scenario, initial)?;
        expect_calls(scenario, 1, calls)?;
        expect_value(scenario, 0x35, value)?;
        expect_inside(scenario, inside)
    })();

    // SAFETY: 自检结束后恢复启动流程约定的关闭中断状态。
    unsafe { set_local_irqs_enabled(false) };
    result
}

/// 验证两层闭包只各执行一次、内层返回原值，且内层退出后仍关闭、外层退出恢复原态。
fn check_nested(initial: bool) -> Result<(), IrqCheckError> {
    let scenario = if initial { "nested-on" } else { "nested-off" };
    // SAFETY: 启动流程尚未设置 sie，切换 SIE 不会触发中断；自检在返回前恢复关闭。
    unsafe { set_local_irqs_enabled(initial) };
    let result = (|| {
        expect_state(scenario, initial)?;

        let mut calls = 0u32;
        let mut outer_inside = !initial;
        let mut inner_inside = !initial;
        let mut after_inner = !initial;
        let value = with_local_irqs_disabled(|| {
            calls += 1;
            outer_inside = local_irqs_enabled();
            let inner_value = with_local_irqs_disabled(|| {
                calls += 1;
                inner_inside = local_irqs_enabled();
                0x36u32
            });
            after_inner = local_irqs_enabled();
            inner_value
        });

        expect_state(scenario, initial)?;
        expect_calls(scenario, 2, calls)?;
        expect_value(scenario, 0x36, value)?;
        expect_inside(scenario, outer_inside)?;
        expect_inside(scenario, inner_inside)?;
        expect_inside(scenario, after_inner)
    })();

    // SAFETY: 自检结束后恢复启动流程约定的关闭中断状态。
    unsafe { set_local_irqs_enabled(false) };
    result
}

/// 读取当前 SIE 并与预期比较；不修改状态。
fn expect_state(scenario: &'static str, expected: bool) -> Result<(), IrqCheckError> {
    let actual = local_irqs_enabled();
    if actual == expected {
        Ok(())
    } else {
        Err(IrqCheckError::State {
            scenario,
            expected,
            actual,
        })
    }
}

/// 要求临界区闭包内保持关闭中断。
fn expect_inside(scenario: &'static str, actual: bool) -> Result<(), IrqCheckError> {
    if actual {
        Err(IrqCheckError::State {
            scenario,
            expected: false,
            actual,
        })
    } else {
        Ok(())
    }
}

/// 核对闭包执行次数。
fn expect_calls(scenario: &'static str, expected: u32, actual: u32) -> Result<(), IrqCheckError> {
    if actual == expected {
        Ok(())
    } else {
        Err(IrqCheckError::CallCount {
            scenario,
            expected,
            actual,
        })
    }
}

/// 核对闭包返回值。返回值是 `u32`，错误路径不会把它清零或替换。
fn expect_value(scenario: &'static str, expected: u32, actual: u32) -> Result<(), IrqCheckError> {
    if actual == expected {
        Ok(())
    } else {
        Err(IrqCheckError::ReturnValue {
            scenario,
            expected,
            actual,
        })
    }
}
