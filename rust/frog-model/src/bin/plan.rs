use std::io::{self, Read};
fn main() {
    let mut input = String::new();
    let result = io::stdin()
        .take(16385)
        .read_to_string(&mut input)
        .map_err(|_| "invalid input".to_string())
        .and_then(|_| {
            if input.len() > 16384 {
                Err("input too large".into())
            } else {
                serde_json::from_str(&input).map_err(|_| "invalid request".into())
            }
        })
        .and_then(frog_model::plan);
    match result {
        Ok(plan) => println!("{}", serde_json::to_string(&plan).unwrap()),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    }
}
