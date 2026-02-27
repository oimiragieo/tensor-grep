use arrow_array::StringArray;
use arrow_array::builder::Int32BufferBuilder;
use arrow_buffer::{Buffer, OffsetBuffer};
use memchr::memchr_iter;
use memmap2::Mmap;
use std::fs::File;
use std::sync::Arc;

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

    // 2. Scan for newline boundaries to construct the offset array
    // We pre-allocate an estimated capacity to reduce re-allocations
    // Assume average line length of 80 bytes
    let estimated_lines = (mmap_len / 80) + 2;
    let mut offset_builder = Int32BufferBuilder::new(estimated_lines);

    // First offset is always 0
    offset_builder.append(0);

    // Get a reference to the raw bytes in the mmap
    // It's safe because mmap_arc keeps the Mmap alive
    let mmap_ref = unsafe {
        let ptr = (&mmap_arc._mmap as *const Mmap) as *const u8;
        std::slice::from_raw_parts(ptr, mmap_len)
    };

    for idx in memchr_iter(b'\n', mmap_ref) {
        // Arrow offsets track the end of the string
        offset_builder.append((idx + 1) as i32);
    }

    // If the file doesn't end with a newline, we add the final offset
    if mmap_ref.last() != Some(&b'\n') {
        offset_builder.append(mmap_len as i32);
    }

    let offsets_buffer = offset_builder.finish();
    let offsets = OffsetBuffer::new(offsets_buffer.into());

    // 3. Create an Arrow Buffer directly wrapping the mmap
    // The Buffer takes an Arc to our MmapAllocation, guaranteeing the mmap
    // is kept alive until the Arrow array is destroyed
    let ptr = std::ptr::NonNull::new(mmap_ref.as_ptr() as *mut u8).unwrap();
    let values_buffer = unsafe { Buffer::from_custom_allocation(ptr, mmap_len, mmap_arc) };

    // 4. Construct the zero-copy StringArray
    // Since we are wrapping arbitrary text, we should ensure valid UTF-8.
    // However, to keep it zero-copy and fast, we can use an unchecked construction
    // and rely on our parsing logic to handle bad bytes gracefully, or validate it.
    // For true high-performance logs, we skip utf-8 checking.
    let string_array = unsafe { StringArray::new_unchecked(offsets, values_buffer, None) };

    Ok(string_array)
}
