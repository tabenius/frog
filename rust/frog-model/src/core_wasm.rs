use std::cell::RefCell;
thread_local! {
    static INPUT: RefCell<Box<[u8;16384]>> = RefCell::new(Box::new([0;16384]));
    static OUTPUT: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
}
#[no_mangle]
pub extern "C" fn frog_input_ptr() -> *mut u8 {
    INPUT.with(|input| input.borrow_mut().as_mut_ptr())
}
#[no_mangle]
pub extern "C" fn frog_output_ptr() -> *const u8 {
    OUTPUT.with(|output| output.borrow().as_ptr())
}
#[no_mangle]
pub extern "C" fn frog_output_len() -> usize {
    OUTPUT.with(|output| output.borrow().len())
}
#[no_mangle]
pub extern "C" fn frog_plan(length: usize) -> u32 {
    let result = if length > 16384 {
        Err("input too large".into())
    } else {
        INPUT.with(|input| {
            serde_json::from_slice(&input.borrow()[..length])
                .map_err(|_| "invalid request".into())
                .and_then(crate::plan)
        })
    };
    let status = u32::from(result.is_err());
    let value = match result {
        Ok(plan) => serde_json::to_value(plan).unwrap(),
        Err(error) => serde_json::json!({"error":error}),
    };
    OUTPUT.with(|output| *output.borrow_mut() = serde_json::to_vec(&value).unwrap());
    status
}
