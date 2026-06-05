use arrow_array::builder::Int32BufferBuilder;
use arrow_array::StringArray;
use arrow_buffer::{Buffer, OffsetBuffer};
use memchr::memchr_iter;
use memmap2::Mmap;
use std::fs::File;
use std::sync::Arc;

fn newline_offsets(mmap_ref: &[u8]) -> Vec<i32> {
    let mut offset_values = vec![0];
    for idx in memchr_iter(b'\n', mmap_ref) {
        offset_values.push((idx + 1) as i32);
    }
    if mmap_ref.last() != Some(&b'\n') {
        offset_values.push(mmap_ref.len() as i32);
    }
    offset_values
}

fn lossy_string_array_from_offsets(mmap_ref: &[u8], offset_values: &[i32]) -> StringArray {
    let lines = offset_values
        .windows(2)
        .map(|window| {
            let start = window[0] as usize;
            let end = window[1] as usize;
            String::from_utf8_lossy(&mmap_ref[start..end]).into_owned()
        })
        .collect::<Vec<_>>();
    StringArray::from_iter_values(lines)
}

pub struct MmapAllocation {
    _mmap: Mmap,
}

pub fn create_arrow_string_array_from_mmap(filepath: &str) -> anyhow::Result<StringArray> {
    // 1. Memory map the file
    let file = File::open(filepath)?;
    let mmap = unsafe { Mmap::map(&file)? };

    let mmap_len = mmap.len();
    if mmap_len == 0 {
        return Ok(StringArray::from_iter_values(std::iter::empty::<String>()));
    }

    // Wrap mmap in Arc so it can be dropped when the Buffer is dropped
    let mmap_arc = Arc::new(MmapAllocation { _mmap: mmap });

    // Ensure we don't exceed i32::MAX for StringArray offsets
    // For files > 2GB, we would need to use LargeStringArray (Int64 offsets)
    if mmap_len > i32::MAX as usize {
        anyhow::bail!(
            "File is too large for standard StringArray (exceeds 2GB). Use LargeStringArray."
        );
    }

    // Get a reference to mapped file bytes (not the Mmap struct memory).
    let mmap_ref: &[u8] = &mmap_arc._mmap;

    // 2. Scan for newline boundaries to construct the offset array
    let offset_values = newline_offsets(mmap_ref);
    if std::str::from_utf8(mmap_ref).is_err() {
        return Ok(lossy_string_array_from_offsets(mmap_ref, &offset_values));
    }

    let mut offset_builder = Int32BufferBuilder::new(offset_values.len());
    for offset in &offset_values {
        offset_builder.append(*offset);
    }
    let offsets = OffsetBuffer::new(offset_builder.finish().into());

    // 3. Create an Arrow Buffer directly wrapping the mmap
    // The Buffer takes an Arc to our MmapAllocation, guaranteeing the mmap
    // is kept alive until the Arrow array is destroyed
    let ptr = std::ptr::NonNull::new(mmap_ref.as_ptr() as *mut u8).unwrap();
    let values_buffer = unsafe { Buffer::from_custom_allocation(ptr, mmap_len, mmap_arc) };

    // 4. Construct the zero-copy StringArray.
    // SAFETY: mmap_ref was validated as UTF-8 above and newline offsets only split on
    // ASCII `\n` boundaries, which are always valid UTF-8 character boundaries.
    let string_array = unsafe { StringArray::new_unchecked(offsets, values_buffer, None) };

    Ok(string_array)
}
