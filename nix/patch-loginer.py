"""Narrow patch against the pinned upstream; fail if expected source changes."""
from pathlib import Path

def replace(path,old,new):
    text=path.read_text()
    if old not in text: raise RuntimeError(f'Upstream patch mismatch: {path}')
    path.write_text(text.replace(old,new))

main=Path('src/main.rs')
replace(main,'    log::info!("Password: {}", password);','')
replace(main,'    log::info!("User ID: {}", user_id);','')
replace(main,'    let user_id = env::var("EGATE_ID").expect("EGATE_ID is not set!");\n    let password = env::var("EGATE_PASSWORD").expect("EGATE_PASSWORD is not set!");', '''    let credentials = std::path::PathBuf::from(env::var("CREDENTIALS_DIRECTORY")?);
    let user_id = std::fs::read_to_string(credentials.join("username"))?.trim_end().to_string();
    let password = std::fs::read_to_string(credentials.join("password"))?;''')
replace(main,'    authenticator.perform_login()?;', '''    if let Err(error) = authenticator.perform_login() {
        let message = error.to_string();
        let state = if message == "Invalid password" { "invalid_password" }
            else if message == "User locked" || message == "User not found" { "locked" }
            else if message == "No WAN address" { "waiting_address" }
            else if message == "Captcha retries exceeded" { "captcha_error" }
            else { "login_error" };
        println!("{}", serde_json::json!({"state": state}));
        std::process::exit(1);
    }
    println!("{}", serde_json::json!({"state": "success"}));''')
auth=Path('src/auth.rs')
replace(auth,'        for ip_address in &self.ip_addresses {','''        if self.ip_addresses.is_empty() { return Err(anyhow!("No WAN address")); }
        for ip_address in &self.ip_addresses {''')
replace(auth,'            .filter_map(|if_addr| match if_addr.addr {','''            .filter(|if_addr| if_addr.name == std::env::var("TARGET_INTERFACE").unwrap_or_else(|_| "wan".into()))
            .filter_map(|if_addr| match if_addr.addr {''')
replace(auth,'        loop {\n            let verify_code', '''        let mut attempts = 0;
        loop {
            attempts += 1;
            if attempts > 3 { return Err(anyhow!("Captcha retries exceeded")); }
            let verify_code''')
