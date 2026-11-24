//! Portable lossless row encoding. This is also the existing inventory hash
//! input: changing it requires a new artifact version, never an in-place change.
use sha2::{Digest, Sha256};

const HEADER: &[u8] = b"frog.migration.typed-row.v1\0";
pub const DATA_FORMAT: &str = "20261020-5";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cell<'a> {
    Null,
    Integer(i64),
    RealBits(u64),
    Text(&'a [u8]),
    Blob(&'a [u8]),
}

impl Cell<'_> {
    pub fn storage_class(&self) -> &'static str {
        match self {
            Self::Null => "null",
            Self::Integer(_) => "integer",
            Self::RealBits(_) => "real",
            Self::Text(_) => "text",
            Self::Blob(_) => "blob",
        }
    }
}

pub fn encode(cells: &[Cell<'_>]) -> Vec<u8> {
    let mut bytes = HEADER.to_vec();
    for cell in cells {
        bytes.extend_from_slice(cell.storage_class().as_bytes());
        match cell {
            Cell::Null => bytes.extend_from_slice(&0u64.to_be_bytes()),
            Cell::Integer(value) => fixed(&mut bytes, &value.to_be_bytes()),
            Cell::RealBits(value) => fixed(&mut bytes, &value.to_be_bytes()),
            Cell::Text(value) | Cell::Blob(value) => fixed(&mut bytes, value),
        }
    }
    bytes
}

fn fixed(output: &mut Vec<u8>, value: &[u8]) {
    output.extend_from_slice(&(value.len() as u64).to_be_bytes());
    output.extend_from_slice(value);
}

/// Borrow raw text/blob bytes; invalid UTF-8 is preserved rather than replaced.
pub fn decode(bytes: &[u8]) -> Result<Vec<Cell<'_>>, &'static str> {
    let mut rest = bytes.strip_prefix(HEADER).ok_or("invalid_preserved_row_header")?;
    let mut cells = Vec::new();
    while !rest.is_empty() {
        let kind = ["null", "integer", "real", "text", "blob"].into_iter()
            .find(|kind| rest.starts_with(kind.as_bytes())).ok_or("invalid_preserved_cell_type")?;
        rest = &rest[kind.len()..];
        let length = rest.get(..8).ok_or("truncated_preserved_cell_length")?;
        let length = usize::try_from(u64::from_be_bytes(length.try_into().unwrap()))
            .map_err(|_| "preserved_cell_length_overflow")?;
        rest = &rest[8..];
        let value = rest.get(..length).ok_or("truncated_preserved_cell")?;
        rest = &rest[length..];
        cells.push(match kind {
            "null" if length == 0 => Cell::Null,
            "integer" if length == 8 => Cell::Integer(i64::from_be_bytes(value.try_into().unwrap())),
            "real" if length == 8 => Cell::RealBits(u64::from_be_bytes(value.try_into().unwrap())),
            "text" => Cell::Text(value),
            "blob" => Cell::Blob(value),
            _ => return Err("invalid_preserved_cell_length"),
        });
    }
    Ok(cells)
}

pub fn row_digest(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}

/// Duplicate row digests are retained; physical row order is irrelevant.
pub fn table_digest(hashes: &mut [[u8; 32]]) -> String {
    hashes.sort_unstable();
    let mut hash = Sha256::new();
    hash.update(b"frog.migration.typed-table.v1\0");
    hash.update((hashes.len() as u64).to_be_bytes());
    for row in hashes { hash.update(row); }
    format!("{:x}", hash.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_roundtrip_covers_large_integers_float_bits_and_non_utf8_text() {
        let cells = [Cell::Null, Cell::Integer(i64::MIN), Cell::Integer(i64::MAX),
            Cell::RealBits((-0.0f64).to_bits()), Cell::RealBits(f64::INFINITY.to_bits()),
            Cell::Text(b"\xff\x00text"), Cell::Blob(b"\xff\x00text"), Cell::Text(b""), Cell::Blob(b"")];
        let encoded = encode(&cells);
        assert_eq!(decode(&encoded).unwrap(), cells);
        // Pin compatibility with the already-published inventory hash bytes.
        assert_eq!(encode(&[Cell::Integer(1)]),
            [HEADER, b"integer", &8u64.to_be_bytes(), &1i64.to_be_bytes()].concat());
        assert_ne!(row_digest(&encode(&[Cell::Text(b"1")])), row_digest(&encode(&[Cell::Blob(b"1")])));
    }

    #[test]
    fn malformed_or_noncanonical_payloads_are_rejected() {
        for payload in [b"bad".as_slice(), HEADER, &[HEADER, b"text"].concat(),
            &[HEADER, b"text", &u64::MAX.to_be_bytes()].concat(),
            &[HEADER, b"null", &1u64.to_be_bytes(), b"x"].concat(),
            &[HEADER, b"integer", &1u64.to_be_bytes(), b"x"].concat()] {
            if payload == HEADER { assert_eq!(decode(payload).unwrap(), []); }
            else { assert!(decode(payload).is_err()); }
        }
        let mut encoded = encode(&[Cell::Text(b"abc")]);
        encoded.pop();
        assert!(decode(&encoded).is_err());
    }
}
