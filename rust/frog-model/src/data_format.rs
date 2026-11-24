//! Persisted data versions are independent of API identifiers and WIT semver.
use serde::{Deserialize, Serialize};
use std::{fmt, str::FromStr};

/// Current inventory artifact format (not a DB cutover or every artifact's version).
pub const CURRENT_DATA_FORMAT: &str = "20261020-3";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct DataFormatVersion {
    date: u32,
    revision: u32,
}

impl FromStr for DataFormatVersion {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let invalid =
            || "data format must be a valid YYYYMMDD-N (positive canonical revision)".to_string();
        let (date, revision) = value.split_once('-').ok_or_else(invalid)?;
        if date.len() != 8
            || !date.bytes().all(|c| c.is_ascii_digit())
            || revision.is_empty()
            || revision.starts_with('0')
            || !revision.bytes().all(|c| c.is_ascii_digit())
        {
            return Err(invalid());
        }
        let date: u32 = date.parse().map_err(|_| invalid())?;
        let revision: u32 = revision.parse().map_err(|_| invalid())?;
        let (year, month, day) = (date / 10000, date / 100 % 100, date % 100);
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        let days = match month {
            1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
            4 | 6 | 9 | 11 => 30,
            2 if leap => 29,
            2 => 28,
            _ => return Err(invalid()),
        };
        if year == 0 || day == 0 || day > days {
            return Err(invalid());
        }
        Ok(Self { date, revision })
    }
}

impl TryFrom<String> for DataFormatVersion {
    type Error = String;
    fn try_from(value: String) -> Result<Self, Self::Error> {
        value.parse()
    }
}

impl From<DataFormatVersion> for String {
    fn from(value: DataFormatVersion) -> Self {
        value.to_string()
    }
}

impl fmt::Display for DataFormatVersion {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:08}-{}", self.date, self.revision)
    }
}

/// A syntactically valid newer/older version is not inventory compatibility.
pub fn require_current(value: &str) -> Result<DataFormatVersion, String> {
    let version: DataFormatVersion = value.parse()?;
    if value != CURRENT_DATA_FORMAT {
        return Err(format!(
            "unsupported data format {value}; explicit migration to {CURRENT_DATA_FORMAT} required"
        ));
    }
    Ok(version)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calendar_and_canonical_revision_validation() {
        for valid in [CURRENT_DATA_FORMAT, "20000229-1", "20240229-4294967295"] {
            let version: DataFormatVersion = valid.parse().unwrap();
            assert_eq!(version.to_string(), valid);
            let json = serde_json::to_string(&version).unwrap();
            assert_eq!(
                serde_json::from_str::<DataFormatVersion>(&json).unwrap(),
                version
            );
        }
        for invalid in [
            "1.0.0",
            "20261020",
            "20261020-0",
            "20261020-01",
            "20261020-+1",
            "20261020-4294967296",
            "19000229-1",
            "20260229-1",
            "20260431-1",
            "20261301-1",
            "00000101-1",
            "20261000-1",
            "20261020-1-2",
            "20261020-１",
        ] {
            assert!(invalid.parse::<DataFormatVersion>().is_err(), "{invalid}");
            assert!(serde_json::from_str::<DataFormatVersion>(&format!("\"{invalid}\"")).is_err());
        }
    }

    #[test]
    fn ordering_is_numeric_but_compatibility_requires_explicit_support() {
        assert!(
            "20261020-10".parse::<DataFormatVersion>().unwrap() > "20261020-3".parse().unwrap()
        );
        assert!(require_current(CURRENT_DATA_FORMAT).is_ok());
        for unsupported in ["20261019-1", "20261020-2", "20261021-1"] {
            assert!(require_current(unsupported).is_err());
        }
    }
}
