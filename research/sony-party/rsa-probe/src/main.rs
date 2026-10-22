//! Read-only DER RSA key search. It does not guess keys or claim decryption.
use rsa::{RsaPrivateKey, RsaPublicKey};
use rsa::pkcs1::{DecodeRsaPrivateKey, DecodeRsaPublicKey};
use rsa::pkcs8::{DecodePrivateKey, DecodePublicKey};
use rsa::traits::PublicKeyParts;
use serde_json::json;
use std::{env, fs};

fn main() {
    let path = env::args().nth(1).expect("Usage: sony-rsa-probe firmware.bin");
    let data = fs::read(&path).expect("read firmware");
    let mut candidates = 0u64;
    let mut hits = Vec::new();
    for offset in 0..data.len().saturating_sub(4) {
        if data[offset] != 0x30 {continue;}
        let first = data[offset+1];
        let (head, len) = if first < 128 {(2,first as usize)} else {
            let n = (first & 127) as usize;
            if n == 0 || n > 3 || offset+2+n > data.len() {continue;}
            let mut len=0usize;
            for b in &data[offset+2..offset+2+n] {len=(len<<8)|*b as usize;}
            (2+n,len)
        };
        if len < 64 || len > 16384 || offset+head+len > data.len() {continue;}
        candidates+=1;
        let der=&data[offset..offset+head+len];
        let public = RsaPublicKey::from_pkcs1_der(der).or_else(|_| RsaPublicKey::from_public_key_der(der));
        if let Ok(key)=public {
            hits.push(json!({"offset":offset,"format":"RSA public DER","bits":key.n().bits()}));
        }
        if let Ok(key)=RsaPrivateKey::from_pkcs1_der(der).or_else(|_| RsaPrivateKey::from_pkcs8_der(der)) {
            if key.validate().is_ok() {
                hits.push(json!({"offset":offset,"format":"RSA private DER (validated)","bits":key.n().bits()}));
            }
        }
    }
    println!("{}",serde_json::to_string_pretty(&json!({"library":"RustCrypto/rsa 0.9.8","input":path,"bytes":data.len(),"der_candidates_tested":candidates,"rsa_keys":hits,"decryption_performed":false,"scope":"Searches unencrypted DER keys only; does not rule out raw, encrypted or external keys"})).unwrap());
}
